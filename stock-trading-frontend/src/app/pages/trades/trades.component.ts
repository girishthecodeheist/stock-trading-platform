import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpParams } from '@angular/common/http';
import { ApiService } from '../../services/api.service';
import { environment } from '../../../environments/environment';

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
  sessions: any[] = [];
  activeSessionId: number | null = null;
  // 'ACTIVE' → active session, 'ALL' → lifetime across sessions, -1 → legacy/unscoped, or a session id number.
  filterSession: 'ACTIVE' | 'ALL' | number = 'ACTIVE';
  loading = true;

  constructor(private api: ApiService, private http: HttpClient) {}

  ngOnInit() {
    this.api.getSessions().subscribe({
      next: (rows) => {
        this.sessions = rows || [];
        const active = this.sessions.find((s: any) => s.status === 'ACTIVE');
        this.activeSessionId = active ? active.id : null;
        this.loadTrades();
      },
      error: () => { this.loadTrades(); }
    });
  }

  onSessionFilterChange() { this.loadTrades(); }

  private paperParams(): HttpParams {
    let params = new HttpParams();
    if (this.filterSession === 'ALL') {
      params = params.set('include_all', 'true');
    } else if (this.filterSession === 'ACTIVE') {
      if (this.activeSessionId != null) {
        params = params.set('session_id', String(this.activeSessionId));
      }
    } else if (typeof this.filterSession === 'number') {
      params = params.set('session_id', String(this.filterSession));
    }
    return params;
  }

  loadTrades() {
    this.loading = true;
    let done = 0;
    const checkDone = () => { done++; if (done >= 2) this.loading = false; };

    this.http.get<any[]>(`${environment.apiUrl}/api/paper-trades`, { params: this.paperParams() }).subscribe({
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
