import { Injectable, NgZone, OnDestroy } from '@angular/core';
import { Observable, Subject } from 'rxjs';

import { ApiService } from './api.service';

/**
 * Singleton SSE manager.
 *
 * Dashboard and Signals both want the same two streams:
 *   - auto-trade engine events (TRADE_PLACED / TRADE_CLOSED / SIGNALS_UPDATED / ...)
 *   - open-trades live prices
 *
 * Before this service, each page opened its own EventSource, doubling the
 * number of SSE connections against the backend and causing duplicate
 * reconnect storms on error. This service keeps exactly one EventSource per
 * stream regardless of how many subscribers are listening, and applies
 * exponential backoff (5s, 10s, 20s, 40s, capped at 60s) on reconnect.
 */
@Injectable({ providedIn: 'root' })
export class SseService implements OnDestroy {
  private autoTradeSource: EventSource | null = null;
  private openTradesSource: EventSource | null = null;

  private autoTradeEvents$ = new Subject<any>();
  private openTradesPrices$ = new Subject<any>();
  private autoTradeConnected$ = new Subject<boolean>();
  private openTradesConnected$ = new Subject<boolean>();

  private autoTradeBackoffMs = 0;
  private openTradesBackoffMs = 0;
  private autoTradeReconnectTimer: any = null;
  private openTradesReconnectTimer: any = null;

  private readonly INITIAL_BACKOFF_MS = 5000;
  private readonly MAX_BACKOFF_MS = 60000;

  constructor(private api: ApiService, private zone: NgZone) {}

  ngOnDestroy() {
    this.disconnectAll();
  }

  /** Open (or return the existing) auto-trade event stream. */
  connectAutoTradeEvents(): Observable<any> {
    if (!this.autoTradeSource) {
      this.openAutoTradeStream();
    }
    return this.autoTradeEvents$.asObservable();
  }

  /** Open (or return the existing) open-trades price stream. */
  connectOpenTradesPrices(): Observable<any> {
    if (!this.openTradesSource) {
      this.openOpenTradesStream();
    }
    return this.openTradesPrices$.asObservable();
  }

  getAutoTradeEvents(): Observable<any> {
    return this.autoTradeEvents$.asObservable();
  }

  getOpenTradesPrices(): Observable<any> {
    return this.openTradesPrices$.asObservable();
  }

  getAutoTradeConnectionState(): Observable<boolean> {
    return this.autoTradeConnected$.asObservable();
  }

  getOpenTradesConnectionState(): Observable<boolean> {
    return this.openTradesConnected$.asObservable();
  }

  disconnectAll() {
    this.closeAutoTradeStream();
    this.closeOpenTradesStream();
  }

  // --- internals --------------------------------------------------------

  private openAutoTradeStream() {
    this.closeAutoTradeStream();
    const url = this.api.getAutoTradeEventsStreamUrl();
    const src = new EventSource(url);
    this.autoTradeSource = src;

    src.onopen = () => {
      this.zone.run(() => {
        this.autoTradeBackoffMs = 0;
        this.autoTradeConnected$.next(true);
      });
    };

    src.onmessage = (event) => {
      this.zone.run(() => {
        try {
          const data = JSON.parse(event.data);
          this.autoTradeEvents$.next(data);
        } catch {
          /* malformed payload — ignore */
        }
      });
    };

    src.onerror = () => {
      this.zone.run(() => {
        this.autoTradeConnected$.next(false);
        this.scheduleAutoTradeReconnect();
      });
    };
  }

  private openOpenTradesStream() {
    this.closeOpenTradesStream();
    const url = this.api.getOpenTradesStreamUrl();
    const src = new EventSource(url);
    this.openTradesSource = src;

    src.onopen = () => {
      this.zone.run(() => {
        this.openTradesBackoffMs = 0;
        this.openTradesConnected$.next(true);
      });
    };

    src.onmessage = (event) => {
      this.zone.run(() => {
        try {
          const data = JSON.parse(event.data);
          this.openTradesPrices$.next(data);
        } catch {
          /* ignore */
        }
      });
    };

    src.onerror = () => {
      this.zone.run(() => {
        this.openTradesConnected$.next(false);
        this.scheduleOpenTradesReconnect();
      });
    };
  }

  private scheduleAutoTradeReconnect() {
    this.closeAutoTradeStream();
    this.autoTradeBackoffMs = this.nextBackoff(this.autoTradeBackoffMs);
    if (this.autoTradeReconnectTimer) clearTimeout(this.autoTradeReconnectTimer);
    this.autoTradeReconnectTimer = setTimeout(
      () => this.openAutoTradeStream(),
      this.autoTradeBackoffMs,
    );
  }

  private scheduleOpenTradesReconnect() {
    this.closeOpenTradesStream();
    this.openTradesBackoffMs = this.nextBackoff(this.openTradesBackoffMs);
    if (this.openTradesReconnectTimer) clearTimeout(this.openTradesReconnectTimer);
    this.openTradesReconnectTimer = setTimeout(
      () => this.openOpenTradesStream(),
      this.openTradesBackoffMs,
    );
  }

  private nextBackoff(current: number): number {
    if (!current) return this.INITIAL_BACKOFF_MS;
    return Math.min(current * 2, this.MAX_BACKOFF_MS);
  }

  private closeAutoTradeStream() {
    if (this.autoTradeReconnectTimer) {
      clearTimeout(this.autoTradeReconnectTimer);
      this.autoTradeReconnectTimer = null;
    }
    if (this.autoTradeSource) {
      try { this.autoTradeSource.close(); } catch { /* noop */ }
      this.autoTradeSource = null;
    }
  }

  private closeOpenTradesStream() {
    if (this.openTradesReconnectTimer) {
      clearTimeout(this.openTradesReconnectTimer);
      this.openTradesReconnectTimer = null;
    }
    if (this.openTradesSource) {
      try { this.openTradesSource.close(); } catch { /* noop */ }
      this.openTradesSource = null;
    }
  }
}
