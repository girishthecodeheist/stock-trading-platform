import { Injectable } from '@angular/core';
import { BehaviorSubject, Observable, of } from 'rxjs';
import { catchError, tap } from 'rxjs/operators';

import { ApiService } from './api.service';

/**
 * Shared dashboard/state service.
 *
 * Components used to each keep a private copy of funds / trades / heatmap /
 * scanner state and issue their own API calls on navigation. Switching
 * between pages therefore cleared the data and triggered a spinner while the
 * component re-fetched everything.
 *
 * This service:
 *   - Keeps the latest data in BehaviorSubjects so page components get the
 *     most recent snapshot immediately on subscribe (no spinner on nav).
 *   - Applies per-resource TTLs so callers can call ``refreshFunds()`` on
 *     every tick of a fast timer without actually hammering the backend.
 *
 * Callers can always pass ``force: true`` to bypass the TTL (e.g. after a
 * user-initiated trade close).
 */
@Injectable({ providedIn: 'root' })
export class StateService {
  // --- Subjects (one per logical resource) --------------------------------
  private paperFundsSubj = new BehaviorSubject<any>(null);
  private liveFundsSubj = new BehaviorSubject<any>(null);
  private tradeModeSubj = new BehaviorSubject<string>('PAPER');
  private fyersConnectedSubj = new BehaviorSubject<boolean>(false);
  private openPaperTradesSubj = new BehaviorSubject<any[]>([]);
  private openLiveTradesSubj = new BehaviorSubject<any[]>([]);
  private heatmapSubj = new BehaviorSubject<any>(null);
  private scannerStatusSubj = new BehaviorSubject<any>(null);
  private autoTradeStatusSubj = new BehaviorSubject<any>(null);
  private signalsSubj = new BehaviorSubject<any[]>([]);

  // --- Observables (read-only) -------------------------------------------
  paperFunds$ = this.paperFundsSubj.asObservable();
  liveFunds$ = this.liveFundsSubj.asObservable();
  tradeMode$ = this.tradeModeSubj.asObservable();
  fyersConnected$ = this.fyersConnectedSubj.asObservable();
  openPaperTrades$ = this.openPaperTradesSubj.asObservable();
  openLiveTrades$ = this.openLiveTradesSubj.asObservable();
  heatmap$ = this.heatmapSubj.asObservable();
  scannerStatus$ = this.scannerStatusSubj.asObservable();
  autoTradeStatus$ = this.autoTradeStatusSubj.asObservable();
  signals$ = this.signalsSubj.asObservable();

  // --- Cache timestamps + TTLs (ms) --------------------------------------
  private tsFunds = 0;
  private tsTradeMode = 0;
  private tsOpenTrades = 0;
  private tsHeatmap = 0;
  private tsScanner = 0;
  private tsAutoTrade = 0;
  private tsSignals = 0;
  private signalsTimeframe = '';

  private readonly TTL_FUNDS = 5000;
  private readonly TTL_TRADE_MODE = 10000;
  private readonly TTL_OPEN_TRADES = 3000;
  private readonly TTL_HEATMAP = 30000;
  private readonly TTL_SCANNER = 5000;
  private readonly TTL_AUTO_TRADE = 5000;
  private readonly TTL_SIGNALS = 30000;

  // In-flight de-dup: if two components call refreshX() at the same time,
  // share the single HTTP request.
  private inflight: Record<string, Observable<any> | undefined> = {};

  constructor(private api: ApiService) {}

  // --- Accessors (latest cached value) -----------------------------------
  get paperFunds() { return this.paperFundsSubj.value; }
  get liveFunds() { return this.liveFundsSubj.value; }
  get tradeMode() { return this.tradeModeSubj.value; }
  get fyersConnected() { return this.fyersConnectedSubj.value; }
  get openPaperTrades() { return this.openPaperTradesSubj.value; }
  get openLiveTrades() { return this.openLiveTradesSubj.value; }
  get heatmap() { return this.heatmapSubj.value; }
  get scannerStatus() { return this.scannerStatusSubj.value; }
  get autoTradeStatus() { return this.autoTradeStatusSubj.value; }
  get signals() { return this.signalsSubj.value; }

  // --- Refresh methods ----------------------------------------------------
  refreshFunds(force = false): Observable<any> {
    const now = Date.now();
    if (!force && now - this.tsFunds < this.TTL_FUNDS && this.paperFundsSubj.value !== null) {
      return of(null);
    }
    const key = 'funds';
    if (this.inflight[key]) return this.inflight[key] as Observable<any>;
    const req = this.api.getCombinedFunds().pipe(
      tap((res: any) => {
        this.paperFundsSubj.next(res?.paper ?? null);
        this.liveFundsSubj.next(res?.live ?? null);
        if (res?.live?.fyers_connected === true) {
          this.fyersConnectedSubj.next(true);
        }
        this.tsFunds = Date.now();
      }),
      catchError(() => of(null)),
    );
    this.inflight[key] = req;
    req.subscribe({ complete: () => delete this.inflight[key] });
    return req;
  }

  refreshTradeMode(force = false): Observable<any> {
    const now = Date.now();
    if (!force && now - this.tsTradeMode < this.TTL_TRADE_MODE && this.tradeModeSubj.value) {
      return of(null);
    }
    const key = 'tradeMode';
    if (this.inflight[key]) return this.inflight[key] as Observable<any>;
    const req = this.api.getTradeMode().pipe(
      tap((res: any) => {
        this.tradeModeSubj.next(res?.mode || 'PAPER');
        this.tsTradeMode = Date.now();
      }),
      catchError(() => of(null)),
    );
    this.inflight[key] = req;
    req.subscribe({ complete: () => delete this.inflight[key] });
    return req;
  }

  refreshFyersStatus(): Observable<any> {
    return this.api.getFyersStatus().pipe(
      tap((res: any) => {
        if (res?.authenticated === true) this.fyersConnectedSubj.next(true);
      }),
      catchError(() => of(null)),
    );
  }

  refreshOpenTrades(force = false): Observable<any> {
    const now = Date.now();
    if (!force && now - this.tsOpenTrades < this.TTL_OPEN_TRADES) {
      return of(null);
    }
    const key = 'openTrades';
    if (this.inflight[key]) return this.inflight[key] as Observable<any>;
    const req = this.api.getPaperTrades('OPEN').pipe(
      tap((res: any) => {
        this.openPaperTradesSubj.next(res || []);
      }),
      catchError(() => of(null)),
    );
    const req2 = this.api.getOpenLiveTrades().pipe(
      tap((res: any) => {
        this.openLiveTradesSubj.next(res?.trades || []);
        this.tsOpenTrades = Date.now();
      }),
      catchError(() => of(null)),
    );
    this.inflight[key] = req;
    req.subscribe({ complete: () => delete this.inflight[key] });
    req2.subscribe();
    return req;
  }

  refreshHeatmap(force = false): Observable<any> {
    const now = Date.now();
    if (!force && now - this.tsHeatmap < this.TTL_HEATMAP && this.heatmapSubj.value) {
      return of(null);
    }
    const key = 'heatmap';
    if (this.inflight[key]) return this.inflight[key] as Observable<any>;
    const req = this.api.getHeatmapLive().pipe(
      tap((res: any) => {
        this.heatmapSubj.next(res);
        this.tsHeatmap = Date.now();
      }),
      catchError(() => of(null)),
    );
    this.inflight[key] = req;
    req.subscribe({ complete: () => delete this.inflight[key] });
    return req;
  }

  refreshScannerStatus(force = false): Observable<any> {
    const now = Date.now();
    if (!force && now - this.tsScanner < this.TTL_SCANNER && this.scannerStatusSubj.value) {
      return of(null);
    }
    const key = 'scanner';
    if (this.inflight[key]) return this.inflight[key] as Observable<any>;
    const req = this.api.getScannerStatus().pipe(
      tap((res: any) => {
        this.scannerStatusSubj.next(res);
        if (res?.engine) this.autoTradeStatusSubj.next(res.engine);
        this.tsScanner = Date.now();
      }),
      catchError(() => of(null)),
    );
    this.inflight[key] = req;
    req.subscribe({ complete: () => delete this.inflight[key] });
    return req;
  }

  refreshAutoTradeStatus(force = false): Observable<any> {
    const now = Date.now();
    if (!force && now - this.tsAutoTrade < this.TTL_AUTO_TRADE && this.autoTradeStatusSubj.value) {
      return of(null);
    }
    const key = 'autoTrade';
    if (this.inflight[key]) return this.inflight[key] as Observable<any>;
    const req = this.api.getAutoTradeStatus().pipe(
      tap((res: any) => {
        this.autoTradeStatusSubj.next(res);
        this.tsAutoTrade = Date.now();
      }),
      catchError(() => of(null)),
    );
    this.inflight[key] = req;
    req.subscribe({ complete: () => delete this.inflight[key] });
    return req;
  }

  refreshSignals(timeframe: string, force = false): Observable<any> {
    const now = Date.now();
    if (
      !force &&
      now - this.tsSignals < this.TTL_SIGNALS &&
      this.signalsTimeframe === timeframe &&
      this.signalsSubj.value.length > 0
    ) {
      return of(null);
    }
    const key = `signals:${timeframe}`;
    if (this.inflight[key]) return this.inflight[key] as Observable<any>;
    const req = this.api.getDashboardScan(undefined, timeframe).pipe(
      tap((res: any) => {
        this.signalsSubj.next(res?.instruments || res?.signals || []);
        this.signalsTimeframe = timeframe;
        this.tsSignals = Date.now();
      }),
      catchError(() => of(null)),
    );
    this.inflight[key] = req;
    req.subscribe({ complete: () => delete this.inflight[key] });
    return req;
  }

  // --- Invalidation (use after user-initiated mutations) -----------------
  invalidateFunds() { this.tsFunds = 0; }
  invalidateOpenTrades() { this.tsOpenTrades = 0; }
  invalidateHeatmap() { this.tsHeatmap = 0; }
  invalidateSignals() { this.tsSignals = 0; }
  invalidateScanner() { this.tsScanner = 0; }
  invalidateAutoTrade() { this.tsAutoTrade = 0; }
  invalidateAll() {
    this.tsFunds = 0;
    this.tsTradeMode = 0;
    this.tsOpenTrades = 0;
    this.tsHeatmap = 0;
    this.tsScanner = 0;
    this.tsAutoTrade = 0;
    this.tsSignals = 0;
  }

  /** Bulk refresh: fast-path resources (funds, trades, scanner status). */
  refreshFast(force = false) {
    this.refreshFunds(force);
    this.refreshOpenTrades(force);
    this.refreshAutoTradeStatus(force);
  }

  /** Bulk refresh: slow-path resources (heatmap, settings-ish). */
  refreshSlow(force = false) {
    this.refreshHeatmap(force);
    this.refreshScannerStatus(force);
    this.refreshTradeMode(force);
  }
}
