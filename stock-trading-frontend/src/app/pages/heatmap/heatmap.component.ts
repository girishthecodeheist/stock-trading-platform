import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { ApiService } from '../../services/api.service';

@Component({
  selector: 'app-heatmap',
  standalone: true,
  imports: [CommonModule, FormsModule, RouterLink],
  templateUrl: './heatmap.component.html',
  styleUrl: './heatmap.component.scss'
})
export class HeatmapComponent implements OnInit {
  allStocks: any[] = [];
  sectors: any[] = [];
  lastPoll: string = '';
  totalStocks = 0;
  loading = false;
  selectedSector: string = '';

  // Backdate simulation
  backdateMode = false;
  selectedDate: string = '';
  backdateSource: string = '';
  backdateError: string = '';

  constructor(private api: ApiService) {}

  ngOnInit() {
    this.selectedDate = this.getTodayStr();
    this.loadHeatmap();
  }

  getTodayStr(): string {
    const d = new Date();
    return d.toISOString().split('T')[0];
  }

  loadHeatmap() {
    this.backdateMode = false;
    this.backdateError = '';
    this.backdateSource = '';
    this.api.getHeatmapLive().subscribe({
      next: (res) => {
        this.allStocks = res.all_stocks || [];
        this.sectors = res.sectors || [];
        this.totalStocks = res.total_stocks || 0;
        this.lastPoll = res.last_poll || '';
      }
    });
  }

  refreshHeatmap() {
    this.loading = true;
    this.backdateMode = false;
    this.api.forceRefreshHeatmap().subscribe({
      next: () => {
        setTimeout(() => { this.loadHeatmap(); this.loading = false; }, 2000);
      },
      error: () => { this.loading = false; }
    });
  }

  loadBackdate() {
    if (!this.selectedDate) return;
    this.loading = true;
    this.backdateError = '';
    this.api.getBackdateSimulation(this.selectedDate).subscribe({
      next: (res) => {
        this.loading = false;
        if (res.success) {
          this.backdateMode = true;
          this.allStocks = res.all_stocks || [];
          this.sectors = res.sectors || [];
          this.totalStocks = res.total_stocks || 0;
          this.backdateSource = res.source || '';
          this.lastPoll = res.date || this.selectedDate;
        } else {
          this.backdateError = res.error || 'Failed to load data for this date';
        }
      },
      error: (err) => {
        this.loading = false;
        this.backdateError = err?.error?.error || 'Failed to load backdate data';
      }
    });
  }

  filterBySector(sector: string) {
    this.selectedSector = this.selectedSector === sector ? '' : sector;
  }

  get filteredStocks(): any[] {
    if (!this.selectedSector) return this.allStocks;
    return this.allStocks.filter(s => s.sector === this.selectedSector);
  }

  getChangeClass(pct: number): string {
    if (pct > 2) return 'strong-gain';
    if (pct > 0) return 'gain';
    if (pct < -2) return 'strong-loss';
    if (pct < 0) return 'loss';
    return '';
  }
}
