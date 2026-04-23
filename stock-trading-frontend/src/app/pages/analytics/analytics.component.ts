import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { HttpClient, HttpParams } from '@angular/common/http';
import { ApiService } from '../../services/api.service';
import { environment } from '../../../environments/environment';

@Component({
  selector: 'app-analytics',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './analytics.component.html',
  styleUrl: './analytics.component.scss'
})
export class AnalyticsComponent implements OnInit {
  paperAnalytics: any = null;
  liveAnalytics: any = null;
  activeTab: string = 'paper';
  loading = true;
  dateFrom: string = '';
  dateTo: string = '';

  sessions: any[] = [];
  activeSessionId: number | null = null;
  // 'ACTIVE' / 'ALL' / -1 (legacy) / numeric session id. Paper tab only.
  filterSession: 'ACTIVE' | 'ALL' | number = 'ACTIVE';

  constructor(private api: ApiService, private http: HttpClient) {}

  ngOnInit() {
    this.api.getSessions().subscribe({
      next: (rows) => {
        this.sessions = rows || [];
        const active = this.sessions.find((s: any) => s.status === 'ACTIVE');
        this.activeSessionId = active ? active.id : null;
        this.loadAnalytics();
      },
      error: () => { this.loadAnalytics(); }
    });
  }

  onSessionFilterChange() { this.loadAnalytics(); }

  private paperParams(): HttpParams {
    let params = new HttpParams();
    if (this.dateFrom) params = params.set('date_from', this.dateFrom);
    if (this.dateTo) params = params.set('date_to', this.dateTo);
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

  loadAnalytics() {
    this.loading = true;
    this.http.get(`${environment.apiUrl}/api/paper-trades/analytics`, { params: this.paperParams() }).subscribe({
      next: (data) => { this.paperAnalytics = data; this.loading = false; },
      error: () => { this.loading = false; }
    });
    this.api.getLiveAnalytics(this.dateFrom, this.dateTo).subscribe({
      next: (data) => { this.liveAnalytics = data; },
      error: () => {}
    });
  }

  get analytics(): any {
    return this.activeTab === 'paper' ? this.paperAnalytics : this.liveAnalytics;
  }

  getPnlClass(val: number): string {
    return val > 0 ? 'positive' : val < 0 ? 'negative' : '';
  }

  getResultClass(result: string): string {
    if (result === 'WIN') return 'badge-win';
    if (result === 'LOSS') return 'badge-loss';
    return 'badge-neutral';
  }

  formatDuration(minutes: number): string {
    if (!minutes) return '-';
    if (minutes < 60) return minutes + 'm';
    if (minutes < 1440) return Math.floor(minutes / 60) + 'h ' + Math.round(minutes % 60) + 'm';
    return Math.floor(minutes / 1440) + 'd ' + Math.floor((minutes % 1440) / 60) + 'h';
  }

  formatSymbol(symbol: string): string {
    return (symbol || '').replace(/-EQ$/, '').replace(/^NSE:/, '');
  }

  get winLossRatio(): number {
    if (!this.analytics || !this.analytics.losses) return 0;
    return this.analytics.wins / this.analytics.losses;
  }

  get profitFactor(): number {
    if (!this.analytics || !this.analytics.avg_loss || this.analytics.avg_loss === 0) return 0;
    return Math.abs(this.analytics.avg_profit / this.analytics.avg_loss);
  }
}
