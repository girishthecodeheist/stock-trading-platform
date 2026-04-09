import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-trades',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './trades.component.html',
  styleUrl: './trades.component.scss'
})
export class TradesComponent implements OnInit {
  paperTrades: any[] = [];
  liveTrades: any[] = [];
  allTrades: any[] = [];
  filterMode: string = 'ALL';
  filterStatus: string = 'ALL';
  loading = true;

  constructor(private api: ApiService) {}

  ngOnInit() { this.loadTrades(); }

  loadTrades() {
    this.loading = true;
    let done = 0;
    const checkDone = () => { done++; if (done >= 2) this.loading = false; };
    this.api.getPaperTrades().subscribe({
      next: (res) => {
        this.paperTrades = (res || []).map((t: any) => ({ ...t, _mode: 'PAPER' }));
        this.mergeAndFilter();
        checkDone();
      },
      error: () => { checkDone(); }
    });
    this.api.getLiveTrades().subscribe({
      next: (res) => {
        this.liveTrades = ((res.trades || res || [])).map((t: any) => ({ ...t, _mode: 'LIVE' }));
        this.mergeAndFilter();
        checkDone();
      },
      error: () => { checkDone(); }
    });
  }

  mergeAndFilter() {
    let all = [...this.paperTrades, ...this.liveTrades];
    if (this.filterMode !== 'ALL') all = all.filter(t => t._mode === this.filterMode);
    if (this.filterStatus !== 'ALL') all = all.filter(t => t.status === this.filterStatus);
    all.sort((a, b) => new Date(b.entry_time || b.created_at).getTime() - new Date(a.entry_time || a.created_at).getTime());
    this.allTrades = all;
  }

  closeTrade(trade: any) {
    if (trade._mode === 'PAPER') {
      this.api.closePaperTrade(trade.id, trade.entry_price, 'MANUAL').subscribe({ next: () => this.loadTrades() });
    } else {
      this.api.closeLiveTrade(trade.id, trade.entry_price, 'MANUAL').subscribe({ next: () => this.loadTrades() });
    }
  }

  getStatusClass(status: string): string {
    return status === 'OPEN' ? 'status-open' : 'status-closed';
  }

  getPnlClass(pnl: number): string {
    if (pnl > 0) return 'positive';
    if (pnl < 0) return 'negative';
    return '';
  }

  getTimeframe(trade: any): string {
    // Try to extract from indicators_snapshot JSON
    try {
      const snap = typeof trade.indicators_snapshot === 'string'
        ? JSON.parse(trade.indicators_snapshot)
        : trade.indicators_snapshot;
      if (snap?.analyzed_timeframe) return snap.analyzed_timeframe;
    } catch (e) {}
    return trade.analyzed_timeframe || '-';
  }

  getAnalysisBasis(trade: any): string {
    try {
      const snap = typeof trade.indicators_snapshot === 'string'
        ? JSON.parse(trade.indicators_snapshot)
        : trade.indicators_snapshot;
      if (snap?.analysis_basis) return snap.analysis_basis;
    } catch (e) {}
    return trade.analysis_basis || '-';
  }
}
