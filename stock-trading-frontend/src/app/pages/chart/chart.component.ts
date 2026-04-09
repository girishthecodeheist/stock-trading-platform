import { Component, OnInit, OnDestroy } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ActivatedRoute } from '@angular/router';
import { ApiService } from '../../services/api.service';
import { ToastService } from '../../services/toast.service';

@Component({
  selector: 'app-chart',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './chart.component.html',
  styleUrl: './chart.component.scss'
})
export class ChartComponent implements OnInit, OnDestroy {
  Math = Math;

  symbol = 'NSE:RELIANCE-EQ';
  timeframe = '1D';
  instrumentType = 'EQUITY';
  searchTerm = '';
  tradeMode = 'PAPER';

  instruments: any[] = [];
  candles: any[] = [];
  signal: any = null;
  indicators: any = null;
  fundamental: any = null;
  sentiment: any = null;
  loading = false;
  analysisWeights = '';
  dataSource = 'database';
  fyersConnected = false;

  // Smart quantity
  calculatedQty: number | null = null;
  qtyBreakdown: any = null;
  showQtyModal = false;
  pendingTradeMode: string = '';

  // Auto refresh
  marketOpen = false;
  private refreshInterval: any;
  private marketCheckInterval: any;

  timeframes = ['1m', '5m', '15m', '1D', '1W', '1Y'];
  segments = ['EQUITY', 'INDEX', 'FUTURE', 'OPTION'];

  searchResults: any[] = [];
  showSearch = false;

  constructor(
    private api: ApiService,
    private route: ActivatedRoute,
    private toast: ToastService
  ) {}

  ngOnInit() {
    this.api.getFyersStatus().subscribe({
      next: (res) => { this.fyersConnected = res.authenticated === true; },
      error: () => {}
    });
    this.api.getTradeMode().subscribe({
      next: (res) => { this.tradeMode = res.mode || 'PAPER'; },
      error: () => {}
    });
    this.route.queryParams.subscribe(params => {
      if (params['symbol']) this.symbol = params['symbol'];
      if (params['timeframe']) this.timeframe = params['timeframe'];
      this.loadData();
    });
    this.loadInstruments();
    this.checkMarketStatus();
    this.setupAutoRefresh();
    this.marketCheckInterval = setInterval(() => {
      this.checkMarketStatus();
      this.setupAutoRefresh();
    }, 60000);
  }

  ngOnDestroy() {
    if (this.refreshInterval) clearInterval(this.refreshInterval);
    if (this.marketCheckInterval) clearInterval(this.marketCheckInterval);
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
    const interval = this.marketOpen ? 10000 : 60000;
    this.refreshInterval = setInterval(() => this.loadData(), interval);
  }

  loadInstruments() {
    this.api.getInstruments({ limit: 100 }).subscribe({
      next: (data) => { this.instruments = data; },
    });
  }

  loadData() {
    this.loading = true;
    this.api.comprehensiveAnalysis(this.symbol, this.timeframe, this.instrumentType).subscribe({
      next: (data) => {
        this.signal = data.signal;
        this.indicators = data.indicators;
        this.fundamental = data.fundamental;
        this.sentiment = data.sentiment;
        this.analysisWeights = data.signal?.weight_description || '';
        if (data.data_source) this.dataSource = data.data_source;
        this.loading = false;
      },
      error: () => {
        this.api.analyzeSymbol(this.symbol, this.timeframe, this.instrumentType).subscribe({
          next: (data) => {
            this.signal = data.signal;
            this.indicators = data.indicators;
            this.fundamental = null;
            this.sentiment = null;
            this.loading = false;
          },
          error: () => { this.loading = false; }
        });
      }
    });

    this.api.getCandles(this.symbol, this.timeframe, 300).subscribe({
      next: (data) => {
        this.candles = data.candles || [];
        this.dataSource = data.source || 'database';
      }
    });
  }

  onSymbolSelect(inst: any) {
    this.symbol = inst.symbol;
    this.instrumentType = inst.segment;
    this.searchTerm = '';
    this.showSearch = false;
    this.loadData();
  }

  onSearch() {
    if (this.searchTerm.length < 1) { this.showSearch = false; return; }
    this.showSearch = true;
    const term = this.searchTerm.toLowerCase();
    this.searchResults = this.instruments.filter((i: any) =>
      i.symbol.toLowerCase().includes(term) ||
      i.name.toLowerCase().includes(term)
    ).slice(0, 10);
  }

  onTimeframeChange() {
    this.loadData();
  }

  // ===== TRADE CREATION WITH AUTO QUANTITY =====

  initiateTrade(mode: string) {
    if (!this.signal) return;
    this.pendingTradeMode = mode;
    const entryPrice = this.signal.entry_price;

    let slPercent: number | undefined;
    let targetPercent: number | undefined;
    if (this.signal.stop_loss && entryPrice) {
      slPercent = Math.abs((entryPrice - this.signal.stop_loss) / entryPrice * 100);
    }
    if (this.signal.target_1 && entryPrice) {
      targetPercent = Math.abs((this.signal.target_1 - entryPrice) / entryPrice * 100);
    }

    this.api.calculateQuantity(entryPrice, slPercent, targetPercent, mode).subscribe({
      next: (res) => {
        this.calculatedQty = res.quantity || 1;
        this.qtyBreakdown = res;
        // Calculate risk:reward ratio
        if (res.sl_per_share && res.target_per_share && res.sl_per_share > 0) {
          this.qtyBreakdown.risk_reward_ratio = res.target_per_share / res.sl_per_share;
        }
        this.showQtyModal = true;
      },
      error: () => {
        this.calculatedQty = 1;
        this.qtyBreakdown = null;
        this.showQtyModal = true;
      }
    });
  }

  confirmTrade() {
    if (!this.signal) return;
    this.showQtyModal = false;
    const qty = this.calculatedQty || 1;
    const side = this.signal.signal.includes('BUY') ? 'BUY' : 'SELL';

    const trade = {
      symbol: this.symbol,
      instrument_type: this.instrumentType,
      timeframe: this.timeframe,
      side: side,
      entry_price: this.signal.entry_price,
      stop_loss: this.signal.stop_loss,
      target: this.signal.target_1,
      quantity: qty,
      signal_confidence: this.signal.confidence,
      signal_reasons: this.signal.reasons,
      indicators_snapshot: this.indicators,
    };

    if (this.pendingTradeMode === 'PAPER') {
      this.api.createPaperTrade(trade).subscribe({
        next: () => {
          this.toast.success('Paper Trade Created',
            side + ' ' + qty + ' x ' + this.formatSymbol(this.symbol) + ' @ \u20B9' + this.signal.entry_price,
            'SL: \u20B9' + this.signal.stop_loss + ' | Target: \u20B9' + this.signal.target_1);
        },
        error: (err: any) => {
          this.toast.error('Trade Failed', 'Could not create paper trade', err?.error?.detail || '');
        }
      });
    } else {
      this.api.createLiveTrade(trade).subscribe({
        next: () => {
          this.toast.success('Live Trade Placed',
            side + ' ' + qty + ' x ' + this.formatSymbol(this.symbol) + ' @ \u20B9' + this.signal.entry_price,
            'SL: \u20B9' + this.signal.stop_loss + ' | Target: \u20B9' + this.signal.target_1);
        },
        error: (err: any) => {
          this.toast.error('Live Trade Failed', 'Could not place live order', err?.error?.detail || '');
        }
      });
    }
  }

  cancelTrade() {
    this.showQtyModal = false;
  }

  // ===== HELPERS =====

  getSignalClass(signal: string): string {
    if (!signal) return 'badge-neutral';
    if (signal.includes('BUY')) return 'badge-buy';
    if (signal.includes('SELL')) return 'badge-sell';
    return 'badge-neutral';
  }

  getFnoClass(action: string): string {
    if (action === 'CALL') return 'fno-call';
    if (action === 'PUT') return 'fno-put';
    return 'fno-neutral';
  }

  getSentimentClass(classification: string): string {
    if (!classification) return '';
    if (classification.includes('BULLISH')) return 'positive';
    if (classification.includes('BEARISH')) return 'negative';
    return '';
  }

  getPatternClass(type: string): string {
    if (type === 'BULLISH') return 'pattern-bullish';
    if (type === 'BEARISH') return 'pattern-bearish';
    return 'pattern-neutral';
  }

  formatPercent(val: any): string {
    if (val === null || val === undefined) return 'N/A';
    const num = typeof val === 'number' ? val : parseFloat(val);
    if (isNaN(num)) return 'N/A';
    const pct = Math.abs(num) < 1 ? num * 100 : num;
    return pct.toFixed(1) + '%';
  }

  formatNumber(val: any): string {
    if (val === null || val === undefined) return 'N/A';
    const num = typeof val === 'number' ? val : parseFloat(val);
    if (isNaN(num)) return 'N/A';
    if (Math.abs(num) >= 1e12) return (num / 1e12).toFixed(1) + 'T';
    if (Math.abs(num) >= 1e9) return (num / 1e9).toFixed(1) + 'B';
    if (Math.abs(num) >= 1e7) return (num / 1e7).toFixed(1) + 'Cr';
    if (Math.abs(num) >= 1e5) return (num / 1e5).toFixed(1) + 'L';
    return num.toFixed(2);
  }

  getIndicatorCount(): number {
    if (!this.indicators) return 0;
    const keys = ['rsi', 'macd_line', 'sma_20', 'sma_50', 'sma_200', 'ema_9', 'ema_20',
      'bb_upper', 'bb_lower', 'atr', 'stoch_k', 'williams_r', 'cci', 'adx', 'mfi',
      'roc', 'vwap', 'supertrend_direction', 'ichimoku_cloud', 'obv_trend',
      'parabolic_sar', 'volume_ratio', 'support_1', 'resistance_1'];
    return keys.filter(k => this.indicators[k] !== undefined && this.indicators[k] !== null).length;
  }

  formatSymbol(symbol: string): string {
    return symbol.replace(/-EQ$/, '').replace(/^NSE:/, '');
  }

  getTrendIcon(): string {
    const ts = this.indicators?.trend_strength;
    if (ts === 'STRONG_UPTREND' || ts === 'UPTREND') return '\u25B2';
    if (ts === 'STRONG_DOWNTREND' || ts === 'DOWNTREND') return '\u25BC';
    return '\u25C6';
  }

  getTrendClass(): string {
    const ts = this.indicators?.trend_strength;
    if (ts === 'STRONG_UPTREND' || ts === 'UPTREND') return 'positive';
    if (ts === 'STRONG_DOWNTREND' || ts === 'DOWNTREND') return 'negative';
    return '';
  }
}
