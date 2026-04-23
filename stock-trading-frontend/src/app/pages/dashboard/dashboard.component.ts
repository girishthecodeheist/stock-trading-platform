import { Component, OnInit, OnDestroy, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { Subscription } from 'rxjs';

import { ApiService } from '../../services/api.service';
import { StateService } from '../../services/state.service';
import { SseService } from '../../services/sse.service';
import { SessionManagerComponent } from '../../components/session-manager/session-manager.component';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink, SessionManagerComponent],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss'
})
export class DashboardComponent implements OnInit, OnDestroy {
  paperFunds: any = null;
  liveFunds: any = null;
  openPaperTrades: any[] = [];
  openLiveTrades: any[] = [];
  tradeMode = 'PAPER';
  fyersConnected = false;
  marketOpen = false;
  livePrices: { [symbol: string]: number } = {};
  autoTradeStatus: any = null;
  signals: any[] = [];
  openTradeCharges: { [tradeId: number]: any } = {};
  editingCapital = false;
  editCapitalAmount: number | null = null;
  savingCapital = false;
  private fastInterval: any;
  private slowInterval: any;
  private marketCheckInterval: any;
  private subs: Subscription[] = [];
  sseConnected = false;
  toastMessage = '';
  toastType = 'info';
  toastVisible = false;
  loading = true;

  constructor(
    private api: ApiService,
    private zone: NgZone,
    private state: StateService,
    private sse: SseService,
  ) {}

  ngOnInit() {
    // Subscribe to shared state first so existing cached data renders
    // immediately on navigation — no spinner flash when coming back to the
    // page.
    this.bindStateSubscriptions();

    // If we already have cached funds from a previous visit, don't show the
    // spinner at all; otherwise show it only for the initial fetch.
    if (this.state.paperFunds !== null || this.state.liveFunds !== null) {
      this.loading = false;
    }

    this.loadAll(true);
    this.checkMarketStatus();
    this.setupAutoRefresh();
    this.connectSSE();
    this.connectAutoTradeEvents();
    this.marketCheckInterval = setInterval(() => {
      this.checkMarketStatus();
      this.setupAutoRefresh();
    }, 60000);
  }

  ngOnDestroy() {
    if (this.fastInterval) clearInterval(this.fastInterval);
    if (this.slowInterval) clearInterval(this.slowInterval);
    if (this.marketCheckInterval) clearInterval(this.marketCheckInterval);
    this.subs.forEach(s => s.unsubscribe());
    this.subs = [];
    // Note: we don't disconnect the shared SSE streams here — they are
    // singletons managed by SseService and other pages may still need them.
  }

  private bindStateSubscriptions() {
    this.subs.push(
      this.state.paperFunds$.subscribe(v => { this.paperFunds = v; }),
      this.state.liveFunds$.subscribe(v => {
        this.liveFunds = v;
        if (v?.fyers_connected === true) this.fyersConnected = true;
      }),
      this.state.tradeMode$.subscribe(v => { this.tradeMode = v; }),
      this.state.fyersConnected$.subscribe(v => { this.fyersConnected = v; }),
      this.state.openPaperTrades$.subscribe(v => {
        const prev = this.openPaperTrades.length;
        this.openPaperTrades = v || [];
        this.fetchLivePricesForTrades();
        if (prev !== this.openPaperTrades.length) this.setupAutoRefresh();
      }),
      this.state.openLiveTrades$.subscribe(v => {
        const prev = this.openLiveTrades.length;
        this.openLiveTrades = v || [];
        this.fetchLivePricesForTrades();
        if (prev !== this.openLiveTrades.length) this.setupAutoRefresh();
      }),
      this.state.autoTradeStatus$.subscribe(v => { this.autoTradeStatus = v; }),
      this.state.signals$.subscribe(v => { this.signals = v || []; }),
    );
  }

  /**
   * Signal quality summary used in the Auto-Trade panel. Prefers the
   * backend-provided tradeable_count / info_only_count if available
   * (once the auto-trade status endpoint is extended), otherwise falls
   * back to computing locally from cached signals using the same three
   * checks the engine uses: ``abs(score) >= min_score``,
   * ``confidence >= min_confidence``, and ``signal != NEUTRAL``.
   */
  get signalQualitySummary(): {
    total: number;
    tradeable: number;
    infoOnly: number;
    techOnly: number;
    comprehensive: number;
    hasData: boolean;
  } {
    const sigs = this.signals || [];

    let tradeable = this.autoTradeStatus?.tradeable_count;
    let infoOnly = this.autoTradeStatus?.info_only_count;
    let total: number;
    if (tradeable != null && infoOnly != null) {
      // Engine-provided counts: authoritative, match ``signals_count``.
      total = this.autoTradeStatus?.signals_count ?? (tradeable + infoOnly);
    } else {
      // Local-computation fallback: derive everything from the same
      // ``sigs`` array so tradeable + infoOnly == total (mixing
      // ``signals_count`` from the engine with a locally-filtered
      // ``tradeable`` would produce an inconsistent info_only count when
      // the engine rescans between Signals-page loads).
      //
      // Use presence-based checks (not ``||``) so an explicit 0 override
      // — "accept all scores / confidences" — isn't silently replaced by
      // the default. This matches the backend's ``resolve_gate`` semantics
      // and the Signals page's ``isTradeable`` fallback.
      const rawMinScore = this.autoTradeStatus?.min_score_for_trade;
      const rawMinConf = this.autoTradeStatus?.min_confidence_for_trade;
      const minScore = rawMinScore != null ? Number(rawMinScore) : 25;
      const minConf = rawMinConf != null ? Number(rawMinConf) : 30;
      tradeable = sigs.filter(s => {
        const sc = Math.abs(Number(s?.score) || 0);
        const conf = Number(s?.confidence) || 0;
        const sig = (s?.signal || s?.signal_type || '').toString().toUpperCase();
        return sc >= minScore && conf >= minConf && sig !== 'NEUTRAL';
      }).length;
      total = sigs.length;
      infoOnly = Math.max(0, total - tradeable);
    }

    // Only render the summary when we actually have signal rows to reason
    // about. The dashboard doesn't call ``refreshSignals`` itself, so
    // ``signals_count`` from autoTradeStatus can be >0 while the local
    // cache is empty (user hasn't opened the Signals page yet). Showing
    // the widget in that state would misleadingly report Tradeable=0.
    const hasData = total > 0 && sigs.length > 0;

    const techOnly = sigs.filter(s => {
      const basis = (s?.analysis_basis || '').toString().toLowerCase();
      return basis === 'technical' || basis === 'heatmap_fallback';
    }).length;
    const comprehensive = sigs.filter(s => {
      const basis = (s?.analysis_basis || '').toString().toLowerCase();
      return basis.includes('fundamental') || basis.includes('sentiment');
    }).length;

    return {
      total,
      tradeable,
      infoOnly,
      techOnly,
      comprehensive,
      hasData,
    };
  }

  connectSSE() {
    // Shared singleton stream via SseService; connecting twice is a no-op.
    this.subs.push(
      this.sse.connectOpenTradesPrices().subscribe(data => {
        if (!data?.prices) return;
        for (const [symbol, priceData] of Object.entries(data.prices)) {
          const pd = priceData as any;
          if (pd && pd.ltp) this.livePrices[symbol] = pd.ltp;
        }
        this.sseConnected = true;
      }),
      this.sse.getOpenTradesConnectionState().subscribe(connected => {
        this.sseConnected = connected;
      }),
    );
  }

  disconnectSSE() {
    // Shared SSE — owned by the service, nothing to tear down here.
  }

  checkMarketStatus() {
    const now = new Date();
    const istOffset = 5.5 * 60 * 60 * 1000;
    const ist = new Date(now.getTime() + istOffset + now.getTimezoneOffset() * 60000);
    const day = ist.getDay();
    const hours = ist.getHours();
    const minutes = ist.getMinutes();
    const totalMins = hours * 60 + minutes;
    this.marketOpen = day >= 1 && day <= 5 && totalMins >= 555 && totalMins <= 930;
  }

  setupAutoRefresh() {
    if (this.fastInterval) clearInterval(this.fastInterval);
    if (this.slowInterval) clearInterval(this.slowInterval);

    // Fast path: funds + open trades + auto-trade status.
    //   - 5s when trades are open (near real-time P&L)
    //   - 15s when market is open but no trades
    //   - 60s when market is closed
    const hasOpenTrades = this.openPaperTrades.length > 0 || this.openLiveTrades.length > 0;
    const fastMs = hasOpenTrades ? 5000 : (this.marketOpen ? 15000 : 60000);
    this.fastInterval = setInterval(() => this.loadFast(), fastMs);

    // Slow path: heatmap / scanner status / trade mode every 30s — these
    // are much cheaper now because the backend caches them at 5-30s TTL,
    // but hitting them on the fast tick is still wasteful.
    this.slowInterval = setInterval(() => this.loadSlow(), 30000);
  }

  toggleAutoTrade() {
    this.api.toggleAutoTrade().subscribe({
      next: (res) => {
        this.autoTradeStatus = { ...this.autoTradeStatus, auto_trade_enabled: res.auto_trade_enabled };
        this.state.invalidateAutoTrade();
        this.state.invalidateScanner();
        this.loadAll(false);
      }
    });
  }

  loadAll(isInitial: boolean = false) {
    if (isInitial) this.loading = true;

    this.state.refreshFunds(isInitial);
    this.state.refreshFyersStatus().subscribe();
    this.state.refreshTradeMode(isInitial);
    this.state.refreshAutoTradeStatus(isInitial);
    this.state.refreshOpenTrades(isInitial);

    if (isInitial) {
      // Drop the spinner once our first wave of fetches completes — exact
      // ordering doesn't matter because the BehaviorSubjects are what the
      // template reads from.
      setTimeout(() => { this.loading = false; }, 400);
    }
  }

  private loadFast() {
    this.state.refreshFunds();
    this.state.refreshOpenTrades();
    this.state.refreshAutoTradeStatus();
    this.fetchOpenTradeCharges();
  }

  private fetchOpenTradeCharges() {
    if (this.openPaperTrades.length === 0) return;
    this.api.getPaperOpenTradeCharges().subscribe({
      next: (rows: any[]) => {
        const map: { [id: number]: any } = {};
        for (const r of rows || []) {
          if (r && r.trade_id != null) map[r.trade_id] = r;
        }
        this.openTradeCharges = map;
      },
      error: () => {},
    });
  }

  private loadSlow() {
    // Heatmap was dropped from the dashboard — the dedicated /heatmap page
    // still refreshes its own data. Keep the scanner/trade-mode pulls so
    // the auto-trade control panel stays accurate.
    this.state.refreshScannerStatus();
    this.state.refreshTradeMode();
  }

  fetchLivePricesForTrades() {
    if (this.sseConnected) return; // SSE handles live prices
    const allTrades = [...this.openPaperTrades, ...this.openLiveTrades];
    if (allTrades.length === 0) return;
    const symbols = [...new Set(allTrades.map((t: any) => t.symbol))];
    const symbolsParam = symbols.join(',');
    this.api.getFyersLivePrices(symbolsParam).subscribe({
      next: (res) => {
        if (res.prices) {
          for (const [symbol, priceData] of Object.entries(res.prices)) {
            const pd = priceData as any;
            if (pd && pd.ltp) {
              this.livePrices[symbol] = pd.ltp;
            }
          }
        }
      },
      error: () => {}
    });
  }

  getLTP(trade: any): number {
    return this.livePrices[trade.symbol] || 0;
  }

  getUnrealizedPnl(trade: any): number {
    const ltp = this.getLTP(trade);
    if (!ltp || !trade.entry_price) return 0;
    const qty = trade.quantity || 1;
    const side = (trade.side || trade.direction || 'BUY').toUpperCase();
    if (side === 'BUY' || side === 'LONG') {
      return (ltp - trade.entry_price) * qty;
    } else {
      return (trade.entry_price - ltp) * qty;
    }
  }

  getUnrealizedPnlPct(trade: any): number {
    const ltp = this.getLTP(trade);
    if (!ltp || !trade.entry_price) return 0;
    const side = (trade.side || trade.direction || 'BUY').toUpperCase();
    if (side === 'BUY' || side === 'LONG') {
      return ((ltp - trade.entry_price) / trade.entry_price) * 100;
    } else {
      return ((trade.entry_price - ltp) / trade.entry_price) * 100;
    }
  }

  closePaperTrade(trade: any) {
    const ltp = this.getLTP(trade);
    const exitPrice = ltp > 0 ? ltp : trade.entry_price;
    this.api.closePaperTrade(trade.id, exitPrice, 'MANUAL').subscribe({
      next: () => {
        this.state.invalidateOpenTrades();
        this.state.invalidateFunds();
        this.loadAll(false);
      }
    });
  }

  closeLiveTrade(trade: any) {
    const ltp = this.getLTP(trade);
    const exitPrice = ltp > 0 ? ltp : trade.entry_price;
    this.api.closeLiveTrade(trade.id, exitPrice, 'MANUAL').subscribe({
      next: () => {
        this.state.invalidateOpenTrades();
        this.state.invalidateFunds();
        this.loadAll(false);
      }
    });
  }

  formatCurrency(val: number): string {
    if (val == null) return '0';
    return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 0 }).format(val);
  }

  formatPnl(val: number): string {
    if (val == null) return '0';
    const sign = val >= 0 ? '+' : '';
    return sign + new Intl.NumberFormat('en-IN', { maximumFractionDigits: 2 }).format(val);
  }

  // F4: inline capital edit.
  editCapital() {
    this.editingCapital = true;
    this.editCapitalAmount = this.paperFunds?.simulated_capital ?? 500000;
  }

  cancelEditCapital() {
    this.editingCapital = false;
    this.editCapitalAmount = null;
  }

  saveCapital() {
    const amt = Number(this.editCapitalAmount);
    if (!Number.isFinite(amt) || amt <= 0) {
      this.showToast('Capital must be a positive number', 'error');
      return;
    }
    this.savingCapital = true;
    this.api.updateSimulatedCapital(amt).subscribe({
      next: () => {
        this.savingCapital = false;
        this.editingCapital = false;
        this.editCapitalAmount = null;
        this.state.invalidateFunds();
        this.loadAll(false);
        this.showToast(`Simulated capital updated to \u20B9${this.formatCurrency(amt)}`, 'success');
      },
      error: (err) => {
        this.savingCapital = false;
        this.showToast(err?.error?.detail || 'Failed to update capital', 'error');
      },
    });
  }

  // F3: estimated round-trip charges lookup for open trades.
  getEstimatedCharges(trade: any): number | null {
    const charges = this.openTradeCharges[trade?.id];
    if (!charges) return null;
    const val = charges.estimated_charges ?? charges.total_charges;
    return val != null ? Number(val) : null;
  }

  getNetUnrealizedPnl(trade: any): number {
    const gross = this.getUnrealizedPnl(trade);
    const est = this.getEstimatedCharges(trade);
    if (est == null) return gross;
    return gross - est;
  }

  // --- Auto-Trade SSE Events ---
  connectAutoTradeEvents() {
    this.subs.push(
      this.sse.connectAutoTradeEvents().subscribe(data => {
        const eventType = data?.type;
        const payload = data?.data || {};
        if (eventType === 'TRADE_PLACED') {
          this.showToast(`Auto-trade placed: ${payload.side} ${payload.symbol} @ ${payload.entry_price}`, 'success');
          this.state.invalidateOpenTrades();
          this.state.invalidateFunds();
          this.loadAll(false);
        } else if (eventType === 'TRADE_CLOSED') {
          const pnl = payload.pnl_amount >= 0 ? `+${payload.pnl_amount}` : `${payload.pnl_amount}`;
          this.showToast(`Auto-trade closed: ${payload.symbol} ${payload.exit_reason} P&L: ${pnl}`, payload.pnl_amount >= 0 ? 'success' : 'error');
          this.state.invalidateOpenTrades();
          this.state.invalidateFunds();
          this.loadAll(false);
        } else if (eventType === 'DAILY_TARGET_MET') {
          this.showToast(`Daily profit target met! P&L: ${payload.total_pnl}`, 'success');
          this.loadAll(false);
        }
      }),
    );
  }

  disconnectAutoTradeEvents() {
    // Shared SSE — owned by the service.
  }

  showToast(message: string, type: string = 'info') {
    this.toastMessage = message;
    this.toastType = type;
    this.toastVisible = true;
    setTimeout(() => { this.toastVisible = false; }, 5000);
  }
}
