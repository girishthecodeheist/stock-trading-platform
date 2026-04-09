import { Component, OnInit, OnDestroy, NgZone } from '@angular/core';
import { CommonModule } from '@angular/common';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-dashboard',
  standalone: true,
  imports: [CommonModule, RouterLink],
  templateUrl: './dashboard.component.html',
  styleUrl: './dashboard.component.scss'
})
export class DashboardComponent implements OnInit, OnDestroy {
  paperFunds: any = null;
  liveFunds: any = null;
  heatmapData: any = null;
  openPaperTrades: any[] = [];
  openLiveTrades: any[] = [];
  topGainers: any[] = [];
  topLosers: any[] = [];
  sectors: any[] = [];
  tradeMode = 'PAPER';
  fyersConnected = false;
  lastHeatmapUpdate: string = '';
  marketOpen = false;
  livePrices: { [symbol: string]: number } = {};
  autoTradeStatus: any = null;
  private refreshInterval: any;
  private marketCheckInterval: any;
  private eventSource: EventSource | null = null;
  private autoTradeEventSource: EventSource | null = null;
  sseConnected = false;
  toastMessage = '';
  toastType = 'info';
  toastVisible = false;
  loading = true;

  constructor(private api: ApiService, private zone: NgZone) {}

  ngOnInit() {
    this.loadAll();
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
    if (this.refreshInterval) clearInterval(this.refreshInterval);
    if (this.marketCheckInterval) clearInterval(this.marketCheckInterval);
    this.disconnectSSE();
    this.disconnectAutoTradeEvents();
  }

  connectSSE() {
    this.disconnectSSE();
    const url = this.api.getOpenTradesStreamUrl();
    this.eventSource = new EventSource(url);
    this.eventSource.onmessage = (event) => {
      this.zone.run(() => {
        try {
          const data = JSON.parse(event.data);
          if (data.prices) {
            for (const [symbol, priceData] of Object.entries(data.prices)) {
              const pd = priceData as any;
              if (pd && pd.ltp) {
                this.livePrices[symbol] = pd.ltp;
              }
            }
            this.sseConnected = true;
          }
        } catch (e) {}
      });
    };
    this.eventSource.onerror = () => {
      this.sseConnected = false;
      // Reconnect after 5s on error
      setTimeout(() => this.connectSSE(), 5000);
    };
  }

  disconnectSSE() {
    if (this.eventSource) {
      this.eventSource.close();
      this.eventSource = null;
      this.sseConnected = false;
    }
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
    if (this.refreshInterval) clearInterval(this.refreshInterval);
    // Funds/trades refresh: 3s when trades are open (real-time P&L), 10s when market open, 30s otherwise
    const hasOpenTrades = this.openPaperTrades.length > 0 || this.openLiveTrades.length > 0;
    const interval = hasOpenTrades ? 3000 : (this.marketOpen ? 10000 : 30000);
    this.refreshInterval = setInterval(() => this.loadAll(), interval);
  }

  toggleAutoTrade() {
    this.api.toggleAutoTrade().subscribe({
      next: (res) => {
        this.autoTradeStatus = { ...this.autoTradeStatus, auto_trade_enabled: res.auto_trade_enabled };
        this.loadAll();
      }
    });
  }

  loadAll() {
    this.loading = true;
    let completed = 0;
    const checkDone = () => { completed++; if (completed >= 4) this.loading = false; };
    this.api.getCombinedFunds().subscribe({
      next: (res) => {
        this.paperFunds = res.paper;
        this.liveFunds = res.live;
        this.fyersConnected = res.live?.fyers_connected === true;
      },
      error: () => { checkDone(); }
    });
    this.api.getFyersStatus().subscribe({
      next: (res) => {
        if (res.authenticated === true) this.fyersConnected = true;
      },
      error: () => {}
    });
    this.api.getTradeMode().subscribe({
      next: (res) => { this.tradeMode = res.mode || 'PAPER'; checkDone(); },
      error: () => { checkDone(); }
    });
    this.api.getAutoTradeStatus().subscribe({
      next: (res) => { this.autoTradeStatus = res; checkDone(); },
      error: () => { checkDone(); }
    });
    this.api.getHeatmapLive().subscribe({
      next: (res) => {
        this.heatmapData = res;
        this.topGainers = res.top_gainers || [];
        this.topLosers = res.top_losers || [];
        this.sectors = res.sectors || [];
        this.lastHeatmapUpdate = res.last_poll || '';
        checkDone();
      },
      error: () => { checkDone(); }
    });
    this.api.getPaperTrades('OPEN').subscribe({
      next: (res) => {
        const prev = this.openPaperTrades.length;
        this.openPaperTrades = res || [];
        this.fetchLivePricesForTrades();
        if (prev !== this.openPaperTrades.length) this.setupAutoRefresh();
      },
      error: () => {}
    });
    this.api.getOpenLiveTrades().subscribe({
      next: (res) => {
        const prev = this.openLiveTrades.length;
        this.openLiveTrades = res.trades || [];
        this.fetchLivePricesForTrades();
        if (prev !== this.openLiveTrades.length) this.setupAutoRefresh();
      },
      error: () => {}
    });
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

  refreshHeatmap() {
    this.api.forceRefreshHeatmap().subscribe({
      next: () => {
        setTimeout(() => {
          this.api.getHeatmapLive().subscribe({
            next: (res) => {
              this.heatmapData = res;
              this.topGainers = res.top_gainers || [];
              this.topLosers = res.top_losers || [];
              this.sectors = res.sectors || [];
              this.lastHeatmapUpdate = res.last_poll || '';
            }
          });
        }, 1000);
      }
    });
  }

  closePaperTrade(trade: any) {
    const ltp = this.getLTP(trade);
    const exitPrice = ltp > 0 ? ltp : trade.entry_price;
    this.api.closePaperTrade(trade.id, exitPrice, 'MANUAL').subscribe({
      next: () => this.loadAll()
    });
  }

  closeLiveTrade(trade: any) {
    const ltp = this.getLTP(trade);
    const exitPrice = ltp > 0 ? ltp : trade.entry_price;
    this.api.closeLiveTrade(trade.id, exitPrice, 'MANUAL').subscribe({
      next: () => this.loadAll()
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

  // --- Auto-Trade SSE Events ---
  connectAutoTradeEvents() {
    this.disconnectAutoTradeEvents();
    const url = this.api.getAutoTradeEventsStreamUrl();
    this.autoTradeEventSource = new EventSource(url);
    this.autoTradeEventSource.onmessage = (event) => {
      this.zone.run(() => {
        try {
          const data = JSON.parse(event.data);
          const eventType = data.type;
          const payload = data.data || {};
          if (eventType === 'TRADE_PLACED') {
            this.showToast(`Auto-trade placed: ${payload.side} ${payload.symbol} @ ${payload.entry_price}`, 'success');
            this.loadAll();
          } else if (eventType === 'TRADE_CLOSED') {
            const pnl = payload.pnl_amount >= 0 ? `+${payload.pnl_amount}` : `${payload.pnl_amount}`;
            this.showToast(`Auto-trade closed: ${payload.symbol} ${payload.exit_reason} P&L: ${pnl}`, payload.pnl_amount >= 0 ? 'success' : 'error');
            this.loadAll();
          } else if (eventType === 'DAILY_TARGET_MET') {
            this.showToast(`Daily profit target met! P&L: ${payload.total_pnl}`, 'success');
            this.loadAll();
          } else if (eventType === 'SIGNALS_UPDATED') {
            // Signals updated, no toast needed
          }
        } catch (e) {}
      });
    };
    this.autoTradeEventSource.onerror = () => {
      // Reconnect after 5s on error
      setTimeout(() => this.connectAutoTradeEvents(), 5000);
    };
  }

  disconnectAutoTradeEvents() {
    if (this.autoTradeEventSource) {
      this.autoTradeEventSource.close();
      this.autoTradeEventSource = null;
    }
  }

  showToast(message: string, type: string = 'info') {
    this.toastMessage = message;
    this.toastType = type;
    this.toastVisible = true;
    setTimeout(() => { this.toastVisible = false; }, 5000);
  }
}
