import { Routes } from '@angular/router';

export const routes: Routes = [
  { path: '', redirectTo: 'dashboard', pathMatch: 'full' },
  { path: 'dashboard', loadComponent: () => import('./pages/dashboard/dashboard.component').then(m => m.DashboardComponent) },
  { path: 'chart', loadComponent: () => import('./pages/chart/chart.component').then(m => m.ChartComponent) },
  { path: 'trades', loadComponent: () => import('./pages/trades/trades.component').then(m => m.TradesComponent) },
  { path: 'signals', loadComponent: () => import('./pages/signals/signals.component').then(m => m.SignalsComponent) },
  { path: 'heatmap', loadComponent: () => import('./pages/heatmap/heatmap.component').then(m => m.HeatmapComponent) },
  { path: 'analytics', loadComponent: () => import('./pages/analytics/analytics.component').then(m => m.AnalyticsComponent) },
  { path: 'settings', loadComponent: () => import('./pages/settings/settings.component').then(m => m.SettingsComponent) },
  { path: 'trade-journal', loadComponent: () => import('./pages/trade-journal/trade-journal.component').then(m => m.TradeJournalComponent) },
  { path: 'paper-trading', redirectTo: 'trades', pathMatch: 'full' },
  { path: '**', redirectTo: 'dashboard' },
];
