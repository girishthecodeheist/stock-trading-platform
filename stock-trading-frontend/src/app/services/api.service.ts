import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { environment } from '../../environments/environment';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private baseUrl = environment.apiUrl;

  constructor(private http: HttpClient) {}

  // Instruments
  getInstruments(params?: { segment?: string; search?: string; limit?: number }): Observable<any[]> {
    let httpParams = new HttpParams();
    if (params?.segment) httpParams = httpParams.set('segment', params.segment);
    if (params?.search) httpParams = httpParams.set('search', params.search);
    if (params?.limit) httpParams = httpParams.set('limit', params.limit.toString());
    return this.http.get<any[]>(`${this.baseUrl}/api/instruments`, { params: httpParams });
  }

  getSegments(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/instruments/segments`);
  }

  // Candles
  getCandles(symbol: string, timeframe: string = '1D', limit: number = 300): Observable<any> {
    const params = new HttpParams()
      .set('symbol', symbol)
      .set('timeframe', timeframe)
      .set('limit', limit.toString());
    return this.http.get(`${this.baseUrl}/api/candles`, { params });
  }

  // Signals
  analyzeSymbol(symbol: string, timeframe: string = '1D', instrumentType: string = 'EQUITY'): Observable<any> {
    const params = new HttpParams()
      .set('symbol', symbol)
      .set('timeframe', timeframe)
      .set('instrument_type', instrumentType);
    return this.http.get(`${this.baseUrl}/api/signals/analyze`, { params });
  }

  getSignalHistory(params?: { symbol?: string; type?: string; status?: string }): Observable<any[]> {
    let httpParams = new HttpParams();
    if (params?.symbol) httpParams = httpParams.set('symbol', params.symbol);
    if (params?.type) httpParams = httpParams.set('type', params.type);
    if (params?.status) httpParams = httpParams.set('status', params.status);
    return this.http.get<any[]>(`${this.baseUrl}/api/signals/history`, { params: httpParams });
  }

  getDashboardScan(segment?: string, timeframe?: string): Observable<any> {
    let params = new HttpParams();
    if (segment) params = params.set('segment', segment);
    if (timeframe) params = params.set('timeframe', timeframe);
    return this.http.get(`${this.baseUrl}/api/signals/dashboard`, { params });
  }

  // Paper Trading
  getPaperTrades(status?: string): Observable<any[]> {
    let params = new HttpParams();
    if (status) params = params.set('status', status);
    return this.http.get<any[]>(`${this.baseUrl}/api/paper-trades`, { params });
  }

  createPaperTrade(trade: any): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/paper-trades`, trade);
  }

  closePaperTrade(tradeId: number, exitPrice: number, exitReason: string = 'Manual close'): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/paper-trades/${tradeId}/close`, {
      exit_price: exitPrice,
      exit_reason: exitReason
    });
  }

  getAnalytics(dateFrom?: string, dateTo?: string): Observable<any> {
    let params = new HttpParams();
    if (dateFrom) params = params.set('date_from', dateFrom);
    if (dateTo) params = params.set('date_to', dateTo);
    return this.http.get(`${this.baseUrl}/api/paper-trades/analytics`, { params });
  }

  // Comprehensive Analysis (Technical + Fundamental + Sentiment)
  comprehensiveAnalysis(symbol: string, timeframe: string = '1D', instrumentType: string = 'EQUITY'): Observable<any> {
    const params = new HttpParams()
      .set('symbol', symbol)
      .set('timeframe', timeframe)
      .set('instrument_type', instrumentType);
    return this.http.get(`${this.baseUrl}/api/analysis/comprehensive`, { params });
  }

  // Fyers API
  getFyersStatus(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/fyers/status`);
  }

  getFyersAuthUrl(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/fyers/auth-url`);
  }

  getFyersLivePrices(symbols?: string): Observable<any> {
    let params = new HttpParams();
    if (symbols) params = params.set('symbols', symbols);
    return this.http.get(`${this.baseUrl}/api/fyers/live-prices`, { params });
  }

  syncFyersHistory(symbol: string, timeframe: string = '1D', days: number = 365): Observable<any> {
    const params = new HttpParams()
      .set('symbol', symbol)
      .set('timeframe', timeframe)
      .set('days', days.toString());
    return this.http.get(`${this.baseUrl}/api/fyers/history`, { params });
  }

  // SSE stream URL for live prices
  getLiveStreamUrl(symbols?: string, interval: number = 3): string {
    let url = `${this.baseUrl}/api/fyers/stream?interval=${interval}`;
    if (symbols) url += `&symbols=${encodeURIComponent(symbols)}`;
    return url;
  }

  // ========== V3.0 NSE Auto Trading APIs ==========

  // Heatmap
  getHeatmapLive(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/heatmap/live`);
  }

  getHeatmapSectors(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/heatmap/sectors`);
  }

  getTopMovers(n: number = 20): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/heatmap/top-movers?n=${n}`);
  }

  forceRefreshHeatmap(): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/v1/heatmap/force-refresh`, {});
  }

  // Trade Mode
  getTradeMode(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/trade-mode`);
  }

  setTradeMode(mode: string): Observable<any> {
    return this.http.put(`${this.baseUrl}/api/v1/trade-mode`, { mode });
  }

  getTradeModeStatus(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/trade-mode/status`);
  }

  // Settings
  getSettings(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/settings`);
  }

  updateSettings(settings: any): Observable<any> {
    return this.http.put(`${this.baseUrl}/api/v1/settings`, settings);
  }

  // Funds
  getPaperFunds(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/funds/paper`);
  }

  getLiveFunds(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/funds/live`);
  }

  getCombinedFunds(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/funds/combined`);
  }

  // Day Limits
  getLimitsStatus(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/limits/status`);
  }

  updatePaperLimits(limits: any): Observable<any> {
    return this.http.put(`${this.baseUrl}/api/v1/limits/paper`, limits);
  }

  updateLiveLimits(limits: any): Observable<any> {
    return this.http.put(`${this.baseUrl}/api/v1/limits/live`, limits);
  }

  resetDayLimits(): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/v1/limits/reset`, {});
  }

  // Live Trades
  getLiveTrades(status?: string): Observable<any> {
    let params = new HttpParams();
    if (status) params = params.set('status', status);
    return this.http.get(`${this.baseUrl}/api/v1/live/trades`, { params });
  }

  getOpenLiveTrades(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/live/trades/open`);
  }

  createLiveTrade(trade: any): Observable<any> {
    return this.http.post(`${this.baseUrl}/api/v1/live/trades`, trade);
  }

  closeLiveTrade(tradeId: number, exitPrice?: number, exitReason?: string): Observable<any> {
    let params = new HttpParams();
    if (exitPrice) params = params.set('exit_price', exitPrice.toString());
    if (exitReason) params = params.set('exit_reason', exitReason);
    return this.http.put(`${this.baseUrl}/api/v1/live/trades/${tradeId}/close`, null, { params });
  }

  getLiveAnalytics(dateFrom?: string, dateTo?: string): Observable<any> {
    let params = new HttpParams();
    if (dateFrom) params = params.set('date_from', dateFrom);
    if (dateTo) params = params.set('date_to', dateTo);
    return this.http.get(`${this.baseUrl}/api/v1/live/analytics`, { params });
  }

  // Scanner
  getScannerStatus(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/scanner/status`);
  }

  toggleScanner(): Observable<any> {
    return this.http.put(`${this.baseUrl}/api/v1/scanner/toggle`, {});
  }

  getScanUniverse(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/scanner/universe`);
  }

  // Auto-Trade Engine
  toggleAutoTrade(): Observable<any> {
    return this.http.put(`${this.baseUrl}/api/v1/scanner/auto-trade/toggle`, {});
  }

  getAutoTradeStatus(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/scanner/auto-trade/status`);
  }

  getAutoTradeLog(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/scanner/auto-trade/log`);
  }

  // SSE stream URL for open trades live prices
  getOpenTradesStreamUrl(interval: number = 2): string {
    return `${this.baseUrl}/api/v1/scanner/open-trades/stream?interval=${interval}`;
  }

  // SSE stream URL for auto-trade events (TRADE_PLACED, TRADE_CLOSED, etc.)
  getAutoTradeEventsStreamUrl(): string {
    return `${this.baseUrl}/api/v1/scanner/auto-trade/events`;
  }

  // Get auto-trade engine signals (top 20 stocks)
  getAutoTradeSignals(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/scanner/auto-trade/signals`);
  }

  // Rejected signals — symbols the engine looked at but refused to place
  // (brokerage filter, capital limit, cooldown, trend conflict, ...).
  getRejectedSignals(limit: number = 50, reason?: string): Observable<any> {
    let params = new HttpParams().set('limit', String(limit));
    if (reason) params = params.set('reason', reason);
    return this.http.get(`${this.baseUrl}/api/v1/scanner/rejected-signals`, { params });
  }

  // Smart Quantity Calculator
  calculateQuantity(entryPrice: number, slPercent?: number, targetPercent?: number, mode?: string): Observable<any> {
    let params = new HttpParams().set('entry_price', entryPrice.toString());
    if (slPercent !== undefined) params = params.set('sl_percent', slPercent.toString());
    if (targetPercent !== undefined) params = params.set('target_percent', targetPercent.toString());
    if (mode) params = params.set('mode', mode);
    return this.http.get(`${this.baseUrl}/api/v1/settings/calculate-quantity`, { params });
  }

  // Backdate Simulation
  getBackdateSimulation(date: string): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/heatmap/backdate?date=${date}`);
  }

  // ========== v3-base feature pack: brokerage / funds / audit ==========

  // F1: intraday vs delivery comparison.
  getBrokerageComparison(
    buyPrice: number,
    sellPrice: number,
    qty: number = 1,
    availableMargin?: number,
  ): Observable<any> {
    let params = new HttpParams()
      .set('buy_price', buyPrice.toString())
      .set('sell_price', sellPrice.toString())
      .set('qty', qty.toString());
    if (availableMargin !== undefined) {
      params = params.set('available_margin', availableMargin.toString());
    }
    return this.http.get(`${this.baseUrl}/api/analysis/brokerage-comparison`, { params });
  }

  // F4: paper-trade simulated capital update.
  updateSimulatedCapital(newCapital: number): Observable<any> {
    return this.http.put(`${this.baseUrl}/api/v1/funds/paper/simulate`, { new_capital: newCapital });
  }

  // F3: per-open-trade estimated charges for the dashboard.
  getPaperOpenTradeCharges(): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/v1/funds/paper/open-trades-charges`);
  }

  // F5: audit trail endpoints.
  getPaperTradeAudit(tradeId: number): Observable<any> {
    return this.http.get(`${this.baseUrl}/api/paper-trades/${tradeId}/audit`);
  }

  getTradeAudit(tradeId: number, tradeType: 'PAPER' | 'LIVE' = 'PAPER'): Observable<any> {
    const params = new HttpParams().set('trade_type', tradeType);
    return this.http.get(`${this.baseUrl}/api/audit/trade/${tradeId}`, { params });
  }

  getDailyAudit(date?: string, eventType?: string): Observable<any> {
    let params = new HttpParams();
    if (date) params = params.set('date', date);
    if (eventType) params = params.set('event_type', eventType);
    return this.http.get(`${this.baseUrl}/api/audit/daily`, { params });
  }

  getSlChangeHistory(dateFrom?: string, dateTo?: string, symbol?: string): Observable<any> {
    let params = new HttpParams();
    if (dateFrom) params = params.set('date_from', dateFrom);
    if (dateTo) params = params.set('date_to', dateTo);
    if (symbol) params = params.set('symbol', symbol);
    return this.http.get(`${this.baseUrl}/api/audit/sl-changes`, { params });
  }
}
