#!/usr/bin/env python3
"""
Enhanced Anti-Matrix Order Book Analysis with Dynamic Thresholds
Real-time adaptation to market conditions
"""

import websocket
import json
import time
import numpy as np
from collections import deque
from binance.client import Client
from datetime import datetime, timedelta
import pytz
import threading
import requests
import logging
from dynamic_thresholds import DynamicThresholds

logger = logging.getLogger(__name__)

class EnhancedAntiMatrixOrderBook:
    def __init__(self, symbol: str, depth: int = 20):
        self.symbol = symbol
        self.depth = depth
        self.order_book_history = deque(maxlen=50)  # Reduced from 100
        self.price_history = deque(maxlen=30)       # Reduced from 50
        self.volume_profile = {'bids': {}, 'asks': {}}
        self.smart_money_patterns = []
        self.market_regime = 'unknown'
        self.session_analysis = {}
        self.last_manipulation_time = None
        
        # Real-time order flow tracking (optimized)
        self.order_flow_delta = deque(maxlen=20)    # Reduced from 50
        self.order_book_momentum = deque(maxlen=10) # Reduced from 20
        self.real_time_data = {
            'bids': deque(maxlen=5),    # Reduced from 10
            'asks': deque(maxlen=5),    # Reduced from 10
            'delta': deque(maxlen=5),   # Reduced from 10
            'momentum': deque(maxlen=5) # Reduced from 10
        }
        self.is_streaming = False
        self.ws = None
        
        # Economic calendar integration
        self.economic_events = []
        self.news_sentiment = 'neutral'
        
        # INTEGRATE DYNAMIC THRESHOLDS
        self.dynamic_thresholds = DynamicThresholds()
        
        # Caching for API optimization
        self._last_order_book_fetch = 0
        self._cached_order_book = None
        self._fetch_interval = 5  # Fetch every 5 seconds max
        
    def start_real_time_streaming(self):
        """Start real-time order book streaming with error handling"""
        if self.is_streaming:
            return
        
        try:
            self.is_streaming = True
            self.ws = websocket.WebSocketApp(
                f"wss://fstream.binance.com/ws/{self.symbol.lower()}@depth20@100ms",
                on_message=self.on_order_book_message,
                on_error=self.on_error,
                on_close=self.on_close,
                on_open=self.on_open
            )
            
            # Start streaming in separate thread
            self.stream_thread = threading.Thread(target=self.ws.run_forever)
            self.stream_thread.daemon = True
            self.stream_thread.start()
            
            logger.info(f"Real-time streaming started for {self.symbol}")
            
        except Exception as e:
            logger.error(f"Failed to start streaming: {e}")
            self.is_streaming = False
    
    def on_order_book_message(self, ws, message):
        """Handle real-time order book updates with error handling"""
        try:
            data = json.loads(message)
            
            # Extract order book data
            bids = [(float(price), float(qty)) for price, qty in data.get('bids', [])]
            asks = [(float(price), float(qty)) for price, qty in data.get('asks', [])]
            
            # Calculate order flow delta
            delta = self.calculate_order_flow_delta(bids, asks)
            
            # Update dynamic thresholds with new data
            if bids and asks:
                mid_price = (bids[0][0] + asks[0][0]) / 2
                total_volume = sum(qty for _, qty in bids[:5]) + sum(qty for _, qty in asks[:5])
                
                self.dynamic_thresholds.update_market_data(
                    price=mid_price,
                    volume=total_volume,
                    order_flow_delta=delta
                )
            
            self.order_flow_delta.append({
                'timestamp': time.time(),
                'delta': delta,
                'bids': bids,
                'asks': asks
            })
            
            # Calculate order book momentum
            momentum = self.calculate_order_book_momentum()
            self.order_book_momentum.append({
                'timestamp': time.time(),
                'momentum': momentum
            })
            
            # Update real-time data (limited size)
            self.real_time_data['bids'].append(bids)
            self.real_time_data['asks'].append(asks)
            self.real_time_data['delta'].append(delta)
            self.real_time_data['momentum'].append(momentum)
            
        except Exception as e:
            logger.error(f"Error processing order book message: {e}")
    
    def on_error(self, ws, error):
        logger.error(f"WebSocket error: {error}")
        self.is_streaming = False
    
    def on_close(self, ws, close_status_code, close_msg):
        logger.info("WebSocket connection closed")
        self.is_streaming = False
    
    def on_open(self, ws):
        logger.info(f"Real-time order book streaming started for {self.symbol}")
    
    def calculate_order_flow_delta(self, bids, asks):
        """Calculate real-time order flow delta (optimized)"""
        if not bids or not asks:
            return 0
        
        # Calculate total bid and ask volume (only top 3 levels for speed)
        total_bid_volume = sum(qty for price, qty in bids[:3])
        total_ask_volume = sum(qty for price, qty in asks[:3])
        
        # Calculate delta
        total_volume = total_bid_volume + total_ask_volume
        if total_volume == 0:
            return 0
        
        delta = (total_bid_volume - total_ask_volume) / total_volume
        return delta
    
    def calculate_order_book_momentum(self):
        """Calculate order book momentum (optimized)"""
        if len(self.order_flow_delta) < 2:
            return 0
        
        # Use only last 2 deltas for faster calculation
        recent_deltas = [d['delta'] for d in list(self.order_flow_delta)[-2:]]
        momentum = recent_deltas[-1] - recent_deltas[0]
        return momentum
    
    def fetch_order_book_cached(self, client: Client):
        """Fetch order book with caching to reduce API calls"""
        current_time = time.time()
        
        # Return cached data if recent enough
        if (self._cached_order_book and 
            current_time - self._last_order_book_fetch < self._fetch_interval):
            return self._cached_order_book
        
        try:
            order_book = client.futures_order_book(symbol=self.symbol, limit=self.depth)
            self._cached_order_book = order_book
            self._last_order_book_fetch = current_time
            return order_book
            
        except Exception as e:
            logger.error(f"Error fetching order book: {e}")
            # Return cached data if available, otherwise None
            return self._cached_order_book if self._cached_order_book else None
    
    def analyze_order_book_anti_matrix_enhanced(self, order_book, current_price=None):
        """Enhanced Anti-Matrix analysis with dynamic thresholds"""
        if not order_book:
            return 'neutral', [], 0
        
        # Update price history
        if current_price:
            self.price_history.append({
                'timestamp': time.time(),
                'price': current_price
            })
        else:
            # Use mid price from order book if no current price provided
            bids = [(float(price), float(qty)) for price, qty in order_book['bids']]
            asks = [(float(price), float(qty)) for price, qty in order_book['asks']]
            if bids and asks:
                mid_price = (bids[0][0] + asks[0][0]) / 2
                self.price_history.append({
                    'timestamp': time.time(),
                    'price': mid_price
                })
        
        # Store order book for historical analysis
        bids = [(float(price), float(qty)) for price, qty in order_book['bids']]
        asks = [(float(price), float(qty)) for price, qty in order_book['asks']]
        
        self.order_book_history.append({
            'timestamp': time.time(),
            'bids': bids,
            'asks': asks,
            'price': current_price
        })
        
        # Calculate order flow delta for this order book
        delta = self.calculate_order_flow_delta(bids, asks)
        self.order_flow_delta.append({
            'timestamp': time.time(),
            'delta': delta
        })
        
        # UPDATE DYNAMIC THRESHOLDS with real data
        if len(self.price_history) > 0:
            current_price = self.price_history[-1]['price']
            total_volume = sum(qty for _, qty in bids[:5]) + sum(qty for _, qty in asks[:5])
            
            self.dynamic_thresholds.update_market_data(
                price=current_price,
                volume=total_volume,
                order_flow_delta=delta
            )
        
        # Update thresholds
        self.dynamic_thresholds.update_thresholds()
        
        # Get current thresholds
        thresholds = self.dynamic_thresholds.get_all_thresholds()
        
        # DETERMINE MARKET REGIME (Fast detection)
        self.detect_market_regime_fast()
        
        # ANALYZE SMART MONEY PATTERNS (Optimized)
        smart_patterns = self.detect_smart_money_patterns_enhanced(thresholds)
        
        # DETECT SMART MONEY FOOTPRINT (Real-time)
        footprint_patterns = self.detect_smart_money_footprint_enhanced(thresholds)
        if footprint_patterns:
            smart_patterns.extend(footprint_patterns)
        
        # ANALYZE CONTEXT-AWARE PATTERNS
        context_patterns = self.analyze_context_aware_patterns_enhanced(thresholds)
        if context_patterns:
            smart_patterns.extend(context_patterns)
        
        # TIMING ANALYSIS
        timing_analysis = self.analyze_timing_patterns_enhanced(thresholds)
        
        # CONTRARIAN BIAS DETERMINATION
        bias, reasoning, confidence = self.determine_contrarian_bias_enhanced(
            smart_patterns, timing_analysis, thresholds
        )
        
        return bias, reasoning, confidence
    
    def detect_market_regime_fast(self):
        """Fast market regime detection with reduced data requirements"""
        if len(self.price_history) < 3:  # Further reduced requirement
            self.market_regime = 'unknown'
            return
        
        # Convert deque to list for slicing
        price_list = list(self.price_history)
        prices = [p['price'] for p in price_list[-3:]]  # Use only 3 prices
        
        # Calculate volatility
        if len(prices) >= 2:
            volatility = np.std(prices) / np.mean(prices)
        else:
            volatility = 0.01  # Default volatility
        
        # Calculate trend
        if len(prices) >= 2:
            short_ma = prices[-1]  # Latest price
            long_ma = np.mean(prices)  # Average of all prices
            trend = short_ma - long_ma
        else:
            trend = 0
        
        # Enhanced regime detection with order flow
        if len(self.order_flow_delta) >= 2:  # Further reduced from 3
            delta_list = list(self.order_flow_delta)
            deltas = [d['delta'] for d in delta_list[-2:]]
            delta_volatility = np.std(deltas) if len(deltas) > 1 else 0
            delta_trend = deltas[-1] - deltas[0] if len(deltas) > 1 else 0
            
            # Determine regime with order flow context
            if volatility > 0.008:  # Further reduced threshold
                if trend > 0 and delta_trend > 0:
                    self.market_regime = 'bull_manipulation'
                elif trend < 0 and delta_trend < 0:
                    self.market_regime = 'bear_manipulation'
                else:
                    self.market_regime = 'high_volatility_consolidation'
            elif volatility < 0.002:  # Further reduced threshold
                if delta_trend > 0.03:  # Further reduced threshold
                    self.market_regime = 'accumulation'
                elif delta_trend < -0.03:  # Further reduced threshold
                    self.market_regime = 'distribution'
                else:
                    self.market_regime = 'consolidation'
            else:
                if trend > 0:
                    self.market_regime = 'distribution'
                else:
                    self.market_regime = 'consolidation'
        else:
            # Fallback to price-only regime detection
            if volatility > 0.008:  # Further reduced threshold
                if trend > 0:
                    self.market_regime = 'bull_manipulation'
                else:
                    self.market_regime = 'bear_manipulation'
            elif volatility < 0.002:  # Further reduced threshold
                self.market_regime = 'accumulation'
            else:
                if trend > 0:
                    self.market_regime = 'distribution'
                else:
                    self.market_regime = 'consolidation'
    
    def detect_smart_money_patterns_enhanced(self, thresholds):
        """Detect patterns with dynamic thresholds (optimized)"""
        patterns = []
        
        if len(self.order_book_history) < 2:  # Reduced from 3
            return patterns
        
        current = self.order_book_history[-1]
        previous = self.order_book_history[-2]
        
        # Only check most important patterns for speed
        # 1. ORDER BOOK LAYERING
        layering = self.detect_order_book_layering_enhanced(current, thresholds)
        if layering:
            patterns.append(layering)
        
        # 2. LIQUIDITY GRABS (Most important)
        liquidity_grab = self.detect_liquidity_grab_enhanced(current, previous, thresholds)
        if liquidity_grab:
            patterns.append(liquidity_grab)
        
        # 3. STOP HUNTING (Most important)
        stop_hunt = self.detect_stop_hunting_enhanced(current, previous, thresholds)
        if stop_hunt:
            patterns.append(stop_hunt)
        
        return patterns
    
    def detect_order_book_layering_enhanced(self, current, thresholds):
        """Detect smart money layering with dynamic thresholds"""
        bids = current['bids']
        asks = current['asks']
        
        # Use dynamic threshold
        layering_sensitivity = thresholds['layering_sensitivity']
        
        # Check for similar volumes across multiple levels
        bid_volumes = [qty for price, qty in bids[:3]]  # Only top 3 levels
        ask_volumes = [qty for price, qty in asks[:3]]
        
        # Add market regime context
        regime_context = f" in {self.market_regime} regime" if self.market_regime != 'unknown' else ""
        
        if len(bid_volumes) >= 2:  # Reduced requirement
            bid_std = np.std(bid_volumes)
            bid_mean = np.mean(bid_volumes)
            if bid_std < bid_mean * layering_sensitivity:
                return {
                    'type': 'bid_layering',
                    'confidence': 3,
                    'reasoning': f'Smart money layering bid orders - {regime_context}'
                }
        
        if len(ask_volumes) >= 2:  # Reduced requirement
            ask_std = np.std(ask_volumes)
            ask_mean = np.mean(ask_volumes)
            if ask_std < ask_mean * layering_sensitivity:
                return {
                    'type': 'ask_layering',
                    'confidence': 3,
                    'reasoning': f'Smart money layering ask orders - {regime_context}'
                }
        
        return None
    
    def detect_liquidity_grab_enhanced(self, current, previous, thresholds):
        """Detect smart money taking retail liquidity with dynamic thresholds"""
        if not previous:
            return None
        
        # Use dynamic threshold
        liquidity_multiplier = thresholds['liquidity_multiplier']
        
        current_bids = current['bids']
        previous_bids = previous['bids']
        
        # Check for large orders that appeared and disappeared
        for price, qty in current_bids[:3]:  # Only top 3 levels
            prev_qty = next((pq for pp, pq in previous_bids if pp == price), 0)
            if qty > prev_qty * liquidity_multiplier:
                if self.is_liquidity_level_fast(price):
                    # Add market regime context
                    regime_context = f" in {self.market_regime} regime" if self.market_regime != 'unknown' else ""
                    return {
                        'type': 'liquidity_grab',
                        'price': price,
                        'confidence': 5,
                        'reasoning': f'Smart money grabbing liquidity at {price:.5f} - {regime_context}'
                    }
        
        return None
    
    def detect_stop_hunting_enhanced(self, current, previous, thresholds):
        """Detect smart money hunting retail stop losses with dynamic thresholds"""
        if not previous:
            return None
        
        current_asks = current['asks']
        previous_asks = previous['asks']
        
        # Check for large ask orders at key levels
        for price, qty in current_asks[:3]:  # Only top 3 levels
            prev_qty = next((pq for pp, pq in previous_asks if pp == price), 0)
            if qty > prev_qty * 2:  # Large increase
                if self.is_stop_loss_level_fast(price):
                    # Add market regime context
                    regime_context = f" in {self.market_regime} regime" if self.market_regime != 'unknown' else ""
                    return {
                        'type': 'stop_hunting',
                        'price': price,
                        'confidence': 4,
                        'reasoning': f'Smart money hunting stops at {price:.5f} - {regime_context}'
                    }
        
        return None
    
    def detect_smart_money_footprint_enhanced(self, thresholds):
        """Detect smart money footprint with dynamic thresholds"""
        if len(self.real_time_data['delta']) < 3:  # Reduced requirement
            return None
        
        # Use dynamic threshold
        momentum_threshold = thresholds['momentum_threshold']
        
        deltas = list(self.real_time_data['delta'])
        momentums = list(self.real_time_data['momentum'])
        
        footprint_patterns = []
        
        # Add market regime context
        regime_context = f" in {self.market_regime} regime" if self.market_regime != 'unknown' else ""
        
        # Pattern 1: Large delta followed by reversal
        if len(deltas) >= 2:  # Reduced requirement
            if abs(deltas[-1]) > 0.2 and abs(deltas[-1] - deltas[-2]) > 0.1:  # Reduced thresholds
                if deltas[-1] > 0:
                    footprint_patterns.append({
                        'type': 'smart_money_buying',
                        'confidence': 4,
                        'reasoning': f'Large bullish delta detected - Smart money buying - {regime_context}'
                    })
                else:
                    footprint_patterns.append({
                        'type': 'smart_money_selling',
                        'confidence': 4,
                        'reasoning': f'Large bearish delta detected - Smart money selling - {regime_context}'
                    })
        
        # Pattern 2: Momentum divergence
        if len(momentums) >= 2:  # Reduced requirement
            if abs(momentums[-1]) > momentum_threshold:
                if momentums[-1] > 0:
                    footprint_patterns.append({
                        'type': 'momentum_bullish',
                        'confidence': 3,
                        'reasoning': f'Positive order book momentum detected - {regime_context}'
                    })
                else:
                    footprint_patterns.append({
                        'type': 'momentum_bearish',
                        'confidence': 3,
                        'reasoning': f'Negative order book momentum detected - {regime_context}'
                    })
        
        return footprint_patterns
    
    def analyze_context_aware_patterns_enhanced(self, thresholds):
        """Analyze patterns with economic context and dynamic thresholds"""
        context_patterns = []
        
        # Check for economic events
        if self.economic_events:
            for event in self.economic_events:
                if event['impact'] == 'high':
                    context_patterns.append({
                        'type': 'high_impact_event',
                        'confidence': 5,
                        'reasoning': f"High impact event: {event['event']}"
                    })
        
        # Check for news timing
        current_time = datetime.now(pytz.UTC)
        if self.is_news_time_fast(current_time):
            context_patterns.append({
                'type': 'news_manipulation',
                'confidence': 4,
                'reasoning': 'News-based manipulation likely'
            })
        
        # Check for session-based patterns
        session = self.get_current_session_fast(current_time)
        if session in ['london_open', 'new_york_open']:
            context_patterns.append({
                'type': 'session_manipulation',
                'confidence': 3,
                'reasoning': f"Session-based manipulation likely ({session})"
            })
        
        return context_patterns
    
    def analyze_timing_patterns_enhanced(self, thresholds):
        """Analyze timing patterns with dynamic thresholds"""
        if len(self.order_book_history) < 2:  # Reduced requirement
            return {'timing': 'unknown', 'confidence': 0}
        
        current_time = datetime.now(pytz.UTC)
        session = self.get_current_session_fast(current_time)
        
        timing_analysis = {
            'session': session,
            'confidence': 0,
            'patterns': []
        }
        
        # Check for session-based manipulation
        if session in ['london_open', 'new_york_open']:
            timing_analysis['confidence'] += 2
            timing_analysis['patterns'].append('Session-based manipulation likely')
        
        # Check for news timing
        if self.is_news_time_fast(current_time):
            timing_analysis['confidence'] += 3
            timing_analysis['patterns'].append('News-based manipulation likely')
        
        return timing_analysis
    
    def determine_contrarian_bias_enhanced(self, smart_patterns, timing_analysis, thresholds):
        """Determine bias using contrarian thinking with dynamic thresholds"""
        reasoning = []
        confidence = 0
        bias = 'neutral'
        
        # Use dynamic confidence threshold
        pattern_confidence_min = thresholds['pattern_confidence_min']
        
        # SMART MONEY PATTERN ANALYSIS (Contrarian Logic)
        for pattern in smart_patterns:
            if pattern['confidence'] >= pattern_confidence_min:
                if pattern['type'] == 'bid_layering':
                    bias = 'bearish'
                    confidence += pattern['confidence']
                    reasoning.append(f"Bid layering detected - Smart money accumulating for dump")
                
                elif pattern['type'] == 'ask_layering':
                    bias = 'bearish' # Should be bearish (anti-matrix)
                    confidence += pattern['confidence']
                    reasoning.append(f"Ask layering detected - Smart money accumulating for pump")
                
                elif pattern['type'] == 'liquidity_grab':
                    bias = 'bearish' # Smart money stop hunting
                    confidence += pattern['confidence']
                    reasoning.append(f"Liquidity grab detected - Smart money stop hunting")
                
                elif pattern['type'] == 'stop_hunting':
                    bias = 'bullish' # Reversal likely after stop hunting
                    confidence += pattern['confidence']
                    reasoning.append(f"Stop hunting detected - Reversal likely")
                
                elif pattern['type'] == 'smart_money_buying':
                    bias = 'bullish'
                    confidence += pattern['confidence']
                    reasoning.append(f"Smart money buying detected")
                
                elif pattern['type'] == 'smart_money_selling':
                    bias = 'bearish'
                    confidence += pattern['confidence']
                    reasoning.append(f"Smart money selling detected")
        
        # TIMING ANALYSIS (Context-Aware)
        if timing_analysis['confidence'] > 0:
            session = timing_analysis['session']
            
            if session in ['london_open', 'new_york_open']:
                confidence += timing_analysis['confidence']
                reasoning.append(f"Session-based manipulation likely ({session})")
                
                # In high liquidity sessions, smart money often does opposite
                if bias == 'bullish':
                    bias = 'bearish'
                elif bias == 'bearish':
                    bias = 'bullish'
        
        # MARKET REGIME ANALYSIS (Non-Linear Thinking)
        if self.market_regime == 'bull_manipulation':
            if bias == 'bearish':
                bias = 'bullish'
                reasoning.append("Bull manipulation regime - Bearish patterns are fake")
        
        elif self.market_regime == 'bear_manipulation':
            if bias == 'bullish':
                bias = 'bearish'
                reasoning.append("Bear manipulation regime - Bullish patterns are fake")
        
        elif self.market_regime == 'accumulation':
            bias = 'bullish'
            confidence += 2
            reasoning.append("Accumulation regime - Smart money buying")
        
        elif self.market_regime == 'distribution':
            bias = 'bearish'
            confidence += 2
            reasoning.append("Distribution regime - Smart money selling")
        
        return bias, reasoning, confidence
    
    # Optimized helper functions
    def is_liquidity_level_fast(self, price):
        """Fast liquidity level detection"""
        if len(self.price_history) < 5:  # Reduced requirement
            return False
        
        price_list = list(self.price_history)
        prices = [p['price'] for p in price_list[-5:]]
        recent_high = max(prices)
        recent_low = min(prices)
        
        if abs(price - recent_high) / recent_high < 0.002:  # Increased tolerance
            return True
        if abs(price - recent_low) / recent_low < 0.002:  # Increased tolerance
            return True
        
        return False
    
    def is_stop_loss_level_fast(self, price):
        """Fast stop loss level detection"""
        if len(self.price_history) < 2:  # Reduced requirement
            return False
        
        price_list = list(self.price_history)
        current_price = price_list[-1]['price']
        
        if price < current_price * 0.995:  # Below current price
            return True
        if price > current_price * 1.005:  # Above current price
            return True
        
        return False
    
    def get_current_session_fast(self, current_time):
        """Fast session detection"""
        hour = current_time.hour
        
        if 2 <= hour < 6:
            return 'asia'
        elif 6 <= hour < 10:
            return 'london_open'
        elif 10 <= hour < 14:
            return 'london_ny_overlap'
        elif 14 <= hour < 18:
            return 'new_york_open'
        elif 18 <= hour < 22:
            return 'new_york_close'
        else:
            return 'low_liquidity'
    
    def is_news_time_fast(self, current_time):
        """Fast news time detection"""
        hour = current_time.hour
        minute = current_time.minute
        
        news_hours = [8, 12, 14, 20]
        if hour in news_hours and minute < 30:
            return True
        
        return False

# Enhanced usage example
def integrate_enhanced_anti_matrix_analysis(symbol: str, start_streaming=True):
    """Integrate enhanced Anti-Matrix order book analysis"""
    client = Client()
    ob_analyzer = EnhancedAntiMatrixOrderBook(symbol)
    
    # Start real-time streaming if requested
    if start_streaming:
        ob_analyzer.start_real_time_streaming()
        time.sleep(2)  # Allow time for data accumulation
    
    # Fetch and analyze order book with caching
    order_book = ob_analyzer.fetch_order_book_cached(client)
    bias, reasoning, confidence = ob_analyzer.analyze_order_book_anti_matrix_enhanced(order_book)
    
    # Get market summary
    market_summary = ob_analyzer.dynamic_thresholds.get_market_summary()
    
    print(f"Enhanced Anti-Matrix Analysis for {symbol}:")
    print(f"Market Regime: {ob_analyzer.market_regime}")
    print(f"Bias: {bias}")
    print(f"Confidence: {confidence}")
    print(f"Reasoning: {reasoning}")
    print(f"Volatility: {market_summary['volatility']:.4f}")
    print(f"Volume Ratio: {market_summary['volume_ratio']:.2f}")
    print(f"Dynamic Thresholds: {market_summary['thresholds']}")
    
    return bias, confidence, reasoning

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Test the enhanced system
    integrate_enhanced_anti_matrix_analysis("dogeusdt") 
