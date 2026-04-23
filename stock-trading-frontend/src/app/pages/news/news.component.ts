import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { ToastService } from '../../services/toast.service';

interface NewsHeadline {
  title: string;
  source: string;
  date: string;
  sentiment_score: number;
  sentiment_label: string;
}

interface NewsItem {
  symbol: string;
  headlines: NewsHeadline[];
  headline_count: number;
  sentiment_classification: string;
  sentiment_score: number;
  bullish_count?: number;
  bearish_count?: number;
  neutral_count?: number;
}

type Tab = 'top_movers' | 'market' | 'symbol';

@Component({
  selector: 'app-news',
  standalone: true,
  imports: [CommonModule, FormsModule],
  templateUrl: './news.component.html',
  styleUrl: './news.component.scss'
})
export class NewsComponent implements OnInit {
  activeTab: Tab = 'market';
  loading = false;
  marketItems: NewsItem[] = [];
  topMoverItems: NewsItem[] = [];
  symbolItem: NewsItem | null = null;

  symbolQuery = '';
  searching = false;
  lastError = '';

  constructor(private api: ApiService, private toast: ToastService) {}

  ngOnInit() {
    this.loadMarket();
  }

  selectTab(tab: Tab) {
    this.activeTab = tab;
    if (tab === 'market' && this.marketItems.length === 0) this.loadMarket();
    if (tab === 'top_movers' && this.topMoverItems.length === 0) this.loadTopMovers();
  }

  loadMarket() {
    this.loading = true;
    this.lastError = '';
    this.api.getMarketNews().subscribe({
      next: (res) => {
        this.marketItems = (res?.items || []).filter((it: NewsItem) => it.headline_count > 0);
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.lastError = 'Failed to load market news';
        this.toast.error('News', this.lastError);
      }
    });
  }

  loadTopMovers() {
    this.loading = true;
    this.lastError = '';
    this.api.getNewsFeed(8).subscribe({
      next: (res) => {
        this.topMoverItems = (res?.items || []).filter((it: NewsItem) => it.headline_count > 0);
        this.loading = false;
      },
      error: () => {
        this.loading = false;
        this.lastError = 'Failed to load news feed';
        this.toast.error('News', this.lastError);
      }
    });
  }

  searchSymbol() {
    const sym = (this.symbolQuery || '').trim();
    if (!sym) return;
    this.activeTab = 'symbol';
    this.searching = true;
    this.symbolItem = null;
    this.lastError = '';
    this.api.getSymbolNews(sym).subscribe({
      next: (res) => {
        this.symbolItem = res as NewsItem;
        this.searching = false;
      },
      error: () => {
        this.searching = false;
        this.lastError = `No news found for "${sym}"`;
        this.toast.error('News', this.lastError);
      }
    });
  }

  sentimentClass(label: string): string {
    const l = (label || '').toUpperCase();
    if (l.includes('BULL')) return 'sent-bull';
    if (l.includes('BEAR')) return 'sent-bear';
    return 'sent-neutral';
  }

  classificationClass(cls: string): string {
    return this.sentimentClass(cls);
  }

  trackByIndex(i: number): number { return i; }

  get currentItems(): NewsItem[] {
    if (this.activeTab === 'market') return this.marketItems;
    if (this.activeTab === 'top_movers') return this.topMoverItems;
    return this.symbolItem ? [this.symbolItem] : [];
  }
}
