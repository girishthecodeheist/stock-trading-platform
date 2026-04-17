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
  }

  refreshNow() {
    this.refreshing = true;
    this.state.invalidateSignals();
    this.state.invalidateScanner();
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
}
