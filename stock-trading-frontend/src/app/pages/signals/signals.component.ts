import { Component, OnInit, OnDestroy, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { ToastService } from '../../services/toast.service';
import { StateService } from '../../services/state.service';
import { SseService } from '../../services/sse.service';

@Component({
  selector: 'app-signals',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './signals.component.html',
  styleUrl: './signals.component.scss'
})
export class SignalsComponent implements OnInit, OnDestroy {
  signals: any[] = [];
  scannerStatus: any = null;
  autoTradeStatus: any = null;
  marketOpen = false;
  filterType = 'ALL';
  sortBy = 'score';
  timeframe = '1D';
  loading = true;
  refreshing = false;
  lastUpdate = '';
  // Blocked signals — answers "I see strong signals, why zero trades?"
  rejected: any[] = [];
  rejectedFilter: string = 'ALL';
  showRejected = true;
  // TTL/dedup guard — loadSignals() fires on a 15s timer + SSE + toggles;
  // we don't want to hammer /rejected-signals every single tick.
  private rejectedLastFetch = 0;
  private rejectedInFlight = false;
  private static readonly REJECTED_TTL_MS = 10_000;
  private refreshInterval: any;
  private marketCheckInterval: any;
  private subs: Subscription[] = [];

  constructor(
    private api: ApiService,
    private toast: ToastService,
    private zone: NgZone,
    private state: StateService,
    private sse: SseService,
  ) {}

  ngOnInit() {
    // Hydrate immediately from cached state if available (no spinner flash
    // on navigation back to this page).
    const cached = this.state.signals;
    if (cached && cached.length > 0) {
      this.signals = cached;
      this.loading = false;
    }
    this.subs.push(
      this.state.signals$.subscribe(v => {
        this.signals = v || [];
        if (this.signals.length > 0) this.loading = false;
      }),
      this.state.scannerStatus$.subscribe(v => {
        this.scannerStatus = v;
        this.autoTradeStatus = v?.engine ?? this.autoTradeStatus;
      }),
      this.state.autoTradeStatus$.subscribe(v => {
        if (v) this.autoTradeStatus = v;
      }),
    );
    this.loadSignals(true);
    this.checkMarketStatus();
    this.setupAutoRefresh();
    this.connectAutoTradeEvents();
    this.marketCheckInterval = setInterval(() => {
      this.checkMarketStatus();
      this.setupAutoRefresh();
    }, 60000);
  }

  ngOnDestroy() {
    if (this.refreshInterval) clearInterval(this.refreshInterval);
    if (this.marketCheckInterval) clearInterval(this.marketCheckInterval);
    this.subs.forEach(s => s.unsubscribe());
    this.subs = [];
    // Shared SSE streams belong to SseService — leave them running.
  }

  connectAutoTradeEvents() {
    this.subs.push(
      this.sse.connectAutoTradeEvents().subscribe(data => {
        if (!data?.type) return;
        if (data.type === 'SIGNALS_UPDATED') {
          // Cheap refresh — invalidate TTL so the next scan reads fresh.
          this.state.invalidateSignals();
          this.loadSignals();
        } else if (data.type === 'TRADE_PLACED') {
          const p = data.data || {};
          this.toast.success('Auto-Trade', `${p.side} ${p.symbol} @ ${p.entry_price}`);
          this.state.invalidateSignals();
          this.loadSignals();
        } else if (data.type === 'TRADE_CLOSED') {
          const p = data.data || {};
          this.toast.info('Trade Closed', `${p.symbol} ${p.exit_reason} P&L: ${p.pnl_amount}`);
          this.state.invalidateSignals();
          this.loadSignals();
        }
      }),
    );
  }

  checkMarketStatus() {
    const now = new Date();
    const istOffset = 5.5 * 60 * 60 * 1000;
    const ist = new Date(now.getTime() + istOffset + now.getTimezoneOffset() * 60000);
    const day = ist.getDay();
    const totalMins = ist.getHours() * 60 + ist.getMinutes();
    this.marketOpen = day >= 1 && day <= 5 && totalMins >= 555 && totalMins <= 930;
  }

  setupAutoRefresh() {
    if (this.refreshInterval) clearInterval(this.refreshInterval);
    // The auto-trade engine already scans every ~120s and pushes
    // SIGNALS_UPDATED via SSE, which drives the real refresh. This interval
    // is only a safety net — slow it down to 15s during market hours and
    // 60s otherwise.
    const interval = this.marketOpen ? 15000 : 60000;
    this.refreshInterval = setInterval(() => this.loadSignals(), interval);
  }

  loadSignals(isInitial: boolean = false) {
    if (isInitial && this.signals.length === 0) this.loading = true;
    this.state.refreshSignals(this.timeframe, isInitial).subscribe({
      next: () => {
        this.lastUpdate = new Date().toLocaleTimeString();
        this.loading = false;
        this.refreshing = false;
      },
      error: () => { this.loading = false; this.refreshing = false; },
    });
    this.state.refreshScannerStatus(isInitial);
    this.loadRejected();
  }

  loadRejected(force: boolean = false) {
    if (this.rejectedInFlight) return;
    const now = Date.now();
    if (!force && now - this.rejectedLastFetch < SignalsComponent.REJECTED_TTL_MS) return;
    this.rejectedInFlight = true;
    this.api.getRejectedSignals(50).subscribe({
      next: (res) => {
        this.rejected = res?.rejections || [];
        this.rejectedLastFetch = Date.now();
        this.rejectedInFlight = false;
      },
      error: () => {
        this.rejectedInFlight = false;
      },
    });
  }

  get filteredRejected(): any[] {
    if (this.rejectedFilter === 'ALL') return this.rejected;
    return this.rejected.filter(r => (r.reason || '') === this.rejectedFilter);
  }

  get rejectedReasons(): string[] {
    const set = new Set<string>();
    for (const r of this.rejected) if (r.reason) set.add(r.reason);
    return Array.from(set).sort();
  }

  setRejectedFilter(r: string) { this.rejectedFilter = r; }

  rejectionLabel(reason: string): string {
    const map: { [k: string]: string } = {
      WEAK_SIGNAL: 'Score below threshold',
      LOW_CONFIDENCE: 'Confidence too low',
      DAILY_LIMIT: 'Daily trade cap reached',
      COOLDOWN: 'Re-entry cooldown',
      TREND_CONFLICT: '15m vs 1D trend conflict',
      LIVE_NOT_CONNECTED: 'Fyers not connected',
      CAPITAL_LIMIT: 'Insufficient capital',
      BROKERAGE_FILTER: 'Not profitable after charges',
      TRADING_HALTED: 'Daily P&L limit hit',
      OPEN_TRADES_FULL: 'Max open trades reached',
      DUPLICATE_SYMBOL: 'Already open on this symbol',
      FYERS_REJECTED: 'Broker rejected order',
      FALLBACK_BLOCKED: 'Heatmap fallback (no indicators)',
      LOW_VOLUME: 'Low volume vs 20-bar average',
      BAD_RR: 'Risk/reward below 1.5',
      TARGET_TOO_TIGHT: 'Target distance too small',
      REGIME_BLOCK: 'Blocked by Nifty regime',
      NEUTRAL_SIGNAL: 'Signal is NEUTRAL',
      SLOT_FULL: 'Slot limit this cycle',
    };
    return map[reason] || reason;
  }

  rejectionChipClass(reason: string): string {
    if (reason === 'BROKERAGE_FILTER') return 'reject-brokerage';
    if (reason === 'CAPITAL_LIMIT') return 'reject-capital';
    if (reason === 'LIVE_NOT_CONNECTED' || reason === 'FYERS_REJECTED') return 'reject-live';
    if (reason === 'COOLDOWN' || reason === 'DAILY_LIMIT') return 'reject-cooldown';
    if (reason === 'REGIME_BLOCK') return 'reject-regime';
    if (reason === 'LOW_VOLUME' || reason === 'BAD_RR' || reason === 'TARGET_TOO_TIGHT') return 'reject-technical';
    if (reason === 'NEUTRAL_SIGNAL') return 'reject-neutral';
    return 'reject-generic';
  }

  /** v5.1: Per-row status chip class for the signals table. */
  statusChipClass(s: any): string {
    const status = (s?.trade_status || '').toUpperCase();
    if (status === 'PLACED') return 'status-placed';
    if (status === 'BLOCKED') return this.rejectionChipClass(s?.block_reason || '');
    if (status === 'NEUTRAL') return 'status-neutral';
    return 'status-pending';
  }

  /** v5.1: Short status label shown in the Analysis column. */
  statusLabel(s: any): string {
    const status = (s?.trade_status || '').toUpperCase();
    if (status === 'PLACED') return 'Placed';
    if (status === 'BLOCKED') return this.rejectionLabel(s?.block_reason || 'BLOCKED');
    if (status === 'NEUTRAL') return 'Neutral — not traded';
    return 'Awaiting placement';
  }

  refreshNow() {
    this.refreshing = true;
    this.state.invalidateSignals();
    this.state.invalidateScanner();
    this.loadRejected(true);
    this.loadSignals();
  }

  setTimeframe(tf: string) {
    this.timeframe = tf;
    this.loading = true;
    this.state.invalidateSignals();
    this.loadSignals(true);
  }

  toggleScanner() {
    this.api.toggleScanner().subscribe({
      next: () => {
        this.toast.info('Scanner', this.scannerStatus?.scanner_running ? 'Scanner paused' : 'Scanner started');
        this.state.invalidateScanner();
        this.loadSignals();
      }
    });
  }

  toggleAutoTrade() {
    this.api.toggleAutoTrade().subscribe({
      next: (res) => {
        this.toast.info('Auto-Trade', res.auto_trade_enabled ? 'Auto-trade ENABLED' : 'Auto-trade DISABLED');
        this.state.invalidateScanner();
        this.state.invalidateAutoTrade();
        this.loadSignals();
      }
    });
  }

  get filteredSignals(): any[] {
    let list = this.signals;
    if (this.filterType !== 'ALL') {
      list = list.filter(s => {
        const sig = (s.signal || '').toUpperCase();
        if (this.filterType === 'BUY') return sig.includes('BUY');
        if (this.filterType === 'SELL') return sig.includes('SELL');
        if (this.filterType === 'NEUTRAL') return sig === 'NEUTRAL';
        return true;
      });
    }
    // Default sort by absolute score (strongest signals first)
    if (this.sortBy === 'score') {
      list = [...list].sort((a, b) => Math.abs(b.score || 0) - Math.abs(a.score || 0));
    } else if (this.sortBy === 'confidence') {
      list = [...list].sort((a, b) => (b.confidence || 0) - (a.confidence || 0));
    } else if (this.sortBy === 'change') {
      list = [...list].sort((a, b) => Math.abs(b.change_pct || 0) - Math.abs(a.change_pct || 0));
    }
    return list;
  }

  setFilter(type: string) { this.filterType = type; }
  setSort(by: string) { this.sortBy = by; }

  getSignalClass(signalType: string): string {
    const t = (signalType || '').toUpperCase();
    if (t.includes('STRONG BUY') || t.includes('STRONG_BUY')) return 'signal-strong-buy';
    if (t.includes('WEAK BUY') || t.includes('WEAK_BUY')) return 'signal-weak-buy';
    if (t.includes('BUY')) return 'signal-buy';
    if (t.includes('STRONG SELL') || t.includes('STRONG_SELL')) return 'signal-strong-sell';
    if (t.includes('WEAK SELL') || t.includes('WEAK_SELL')) return 'signal-weak-sell';
    if (t.includes('SELL')) return 'signal-sell';
    return 'signal-neutral';
  }

  getSignalIcon(signalType: string): string {
    const t = (signalType || '').toUpperCase();
    if (t.includes('STRONG BUY')) return '\u2B06\uFE0F';
    if (t.includes('BUY')) return '\u2197\uFE0F';
    if (t.includes('STRONG SELL')) return '\u2B07\uFE0F';
    if (t.includes('SELL')) return '\u2198\uFE0F';
    return '\u2796';
  }

  /**
   * A signal is "tradeable" when it meets the auto-trade engine's gating
   * thresholds. Anything that doesn't meet these thresholds is INFO-ONLY
   * — visible on the Signals page for context, but the engine will skip it.
   *
   * Thresholds are read from the engine's status payload (so they track
   * ``gate_overrides`` edits made on the Indicators Control / Settings
   * page). The fallbacks match the engine defaults — ``MIN_SCORE_FOR_TRADE
   * = 25`` and ``MIN_CONFIDENCE_FOR_TRADE = 30`` in
   * ``auto_trade_engine.py`` — so the badge is still correct when status
   * hasn't loaded yet.
   */
  isTradeable(signal: any): boolean {
    if (!signal) return false;
    const score = Number(signal.score) || 0;
    const confidence = Number(signal.confidence) || 0;
    const type = (signal.signal || signal.signal_type || '').toString().toUpperCase();
    const statusMinScore = this.autoTradeStatus?.min_score_for_trade;
    const statusMinConf = this.autoTradeStatus?.min_confidence_for_trade;
    const minScore = statusMinScore != null ? Number(statusMinScore) : 25;
    const minConf = statusMinConf != null ? Number(statusMinConf) : 30;
    return Math.abs(score) >= minScore && confidence >= minConf && type !== 'NEUTRAL';
  }

  /**
   * Map the raw analysis_basis emitted by the backend to a short, readable
   * tag for the table ("TECH", "TECH+FUND", "TECH+FUND+SENT", ...).
   */
  getAnalysisBasisLabel(basis: string): string {
    const key = (basis || '').toLowerCase().trim();
    const map: { [k: string]: string } = {
      'technical': 'TECH',
      'technical+fundamental': 'TECH+FUND',
      'technical+sentiment': 'TECH+SENT',
      'technical+fundamental+sentiment': 'TECH+FUND+SENT',
      'heatmap_fallback': 'HEATMAP',
      'combined': 'COMBINED',
      'unknown': 'UNKNOWN',
    };
    if (map[key]) return map[key];
    if (!key) return '';
    // Fallback: crude token-based abbreviation for any future combinations.
    return key
      .split(/[+_\s]+/)
      .map(t => {
        if (t.startsWith('tech')) return 'TECH';
        if (t.startsWith('fund')) return 'FUND';
        if (t.startsWith('sent')) return 'SENT';
        if (t.startsWith('heat')) return 'HEATMAP';
        return t.toUpperCase();
      })
      .join('+');
  }

  /** True when at least one signal in the current result has a non-zero
   *  technical_score — used to conditionally show the Tech Score column.
   */
  get hasTechScores(): boolean {
    return (this.signals || []).some(
      s => s && s.technical_score !== undefined && s.technical_score !== null && s.technical_score !== 0,
    );
  }

  /** True when at least one signal has a non-zero sentiment_score. */
  get hasSentScores(): boolean {
    return (this.signals || []).some(
      s => s && s.sentiment_score !== undefined && s.sentiment_score !== null && s.sentiment_score !== 0,
    );
  }
}
