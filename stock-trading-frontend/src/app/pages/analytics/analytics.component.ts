import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';

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

  constructor(private api: ApiService) {}

  ngOnInit() { this.loadAnalytics(); }

  loadAnalytics() {
    this.loading = true;
    this.api.getAnalytics(this.dateFrom, this.dateTo).subscribe({
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
