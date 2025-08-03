#!/usr/bin/env python3
"""
Dynamic Thresholds System for Anti-Matrix Trading
Adapts sensitivity based on real-time market conditions
"""

import numpy as np
import pandas as pd
from collections import deque
import time
import logging
from datetime import datetime, timedelta
import pytz

logger = logging.getLogger(__name__)

class DynamicThresholds:
    def __init__(self):
        # Base thresholds (starting values)
        self.base_thresholds = {
            'layering_sensitivity': 0.2,
            'iceberg_consistency': 0.8,
            'liquidity_multiplier': 3.0,
            'momentum_threshold': 0.5,
            'volume_ratio': 1.5,
            'price_change_threshold': 0.002,
            'order_flow_delta_threshold': 0.1,
            'pattern_confidence_min': 2,
            'signal_confidence_min': 3
        }
        
        # Current dynamic thresholds
        self.current_thresholds = self.base_thresholds.copy()
        
        # Market condition trackers
        self.volatility_history = deque(maxlen=50)
        self.volume_history = deque(maxlen=50)
        self.order_flow_history = deque(maxlen=50)
        self.price_history = deque(maxlen=100)
        self.regime_history = deque(maxlen=20)
        
        # Adaptation factors
        self.adaptation_factors = {
            'volatility': 1.0,
            'volume': 1.0,
            'order_flow': 1.0,
            'market_regime': 1.0,
            'session': 1.0,
            'time_of_day': 1.0
        }
        
        # Market regime definitions
        self.regime_thresholds = {
            'high_volatility': {'volatility': 0.02, 'multiplier': 0.7},
            'low_volatility': {'volatility': 0.005, 'multiplier': 1.3},
            'normal_volatility': {'volatility': 0.01, 'multiplier': 1.0},
            'accumulation': {'volume_ratio': 1.2, 'multiplier': 0.8},
            'distribution': {'volume_ratio': 1.2, 'multiplier': 1.2},
            'manipulation': {'order_flow_delta': 0.3, 'multiplier': 0.6}
        }
        
        # Session multipliers
        self.session_multipliers = {
            'asia': 1.1,
            'london_open': 0.9,
            'london_ny_overlap': 0.8,
            'new_york_open': 0.9,
            'new_york_close': 1.0,
            'low_liquidity': 1.3
        }
        
        # Time-based adjustments
        self.time_multipliers = {
            'pre_news': 0.8,      # 30 minutes before news
            'news_time': 0.6,      # During news
            'post_news': 1.2,      # 30 minutes after news
            'weekend': 1.4,        # Weekend trading
            'normal': 1.0
        }
    
    def update_market_data(self, price, volume, order_flow_delta=None, timestamp=None):
        """Update market data for threshold calculations"""
        if timestamp is None:
            timestamp = time.time()
        
        # Update price history
        self.price_history.append({
            'timestamp': timestamp,
            'price': price
        })
        
        # Update volume history
        self.volume_history.append({
            'timestamp': timestamp,
            'volume': volume
        })
        
        # Update order flow if available
        if order_flow_delta is not None:
            self.order_flow_history.append({
                'timestamp': timestamp,
                'delta': order_flow_delta
            })
    
    def calculate_volatility(self):
        """Calculate current market volatility"""
        if len(self.price_history) < 10:
            return 0.01  # Default volatility
        
        prices = [p['price'] for p in list(self.price_history)[-20:]]
        returns = np.diff(prices) / prices[:-1]
        volatility = np.std(returns)
        
        self.volatility_history.append(volatility)
        return volatility
    
    def calculate_volume_profile(self):
        """Calculate volume profile metrics"""
        if len(self.volume_history) < 10:
            return {'avg_volume': 1000, 'volume_ratio': 1.0}
        
        recent_volumes = [v['volume'] for v in list(self.volume_history)[-10:]]
        avg_volume = np.mean(recent_volumes)
        current_volume = recent_volumes[-1] if recent_volumes else avg_volume
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1.0
        
        return {
            'avg_volume': avg_volume,
            'volume_ratio': volume_ratio,
            'volume_trend': 'increasing' if volume_ratio > 1.2 else 'decreasing' if volume_ratio < 0.8 else 'stable'
        }
    
    def calculate_order_flow_metrics(self):
        """Calculate order flow metrics"""
        if len(self.order_flow_history) < 5:
            return {'avg_delta': 0, 'delta_volatility': 0.1}
        
        deltas = [of['delta'] for of in list(self.order_flow_history)[-10:]]
        avg_delta = np.mean(deltas)
        delta_volatility = np.std(deltas)
        
        return {
            'avg_delta': avg_delta,
            'delta_volatility': delta_volatility,
            'delta_trend': 'bullish' if avg_delta > 0.1 else 'bearish' if avg_delta < -0.1 else 'neutral'
        }
    
    def detect_market_regime(self):
        """Detect current market regime"""
        volatility = self.calculate_volatility()
        volume_profile = self.calculate_volume_profile()
        order_flow = self.calculate_order_flow_metrics()
        
        # Determine regime based on multiple factors
        if volatility > self.regime_thresholds['high_volatility']['volatility']:
            regime = 'high_volatility'
        elif volatility < self.regime_thresholds['low_volatility']['volatility']:
            regime = 'low_volatility'
        elif abs(order_flow['avg_delta']) > self.regime_thresholds['manipulation']['order_flow_delta']:
            regime = 'manipulation'
        elif volume_profile['volume_ratio'] > self.regime_thresholds['accumulation']['volume_ratio']:
            regime = 'accumulation' if order_flow['avg_delta'] > 0 else 'distribution'
        else:
            regime = 'normal_volatility'
        
        self.regime_history.append(regime)
        return regime
    
    def get_current_session(self):
        """Get current trading session"""
        current_time = datetime.now(pytz.UTC)
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
    
    def get_time_multiplier(self):
        """Get time-based multiplier"""
        current_time = datetime.now(pytz.UTC)
        hour = current_time.hour
        minute = current_time.minute
        
        # Check for news times (simplified)
        news_hours = [8, 12, 14, 20]
        for news_hour in news_hours:
            if abs(hour - news_hour) <= 1:
                if abs(minute - 0) <= 30:
                    return self.time_multipliers['news_time']
                elif hour < news_hour:
                    return self.time_multipliers['pre_news']
                else:
                    return self.time_multipliers['post_news']
        
        # Check for weekend
        if current_time.weekday() >= 5:  # Saturday/Sunday
            return self.time_multipliers['weekend']
        
        return self.time_multipliers['normal']
    
    def calculate_adaptation_factors(self):
        """Calculate adaptation factors based on market conditions"""
        regime = self.detect_market_regime()
        session = self.get_current_session()
        time_mult = self.get_time_multiplier()
        
        # Volatility adaptation
        volatility = self.calculate_volatility()
        if volatility > 0.02:  # High volatility
            self.adaptation_factors['volatility'] = 0.7  # More sensitive
        elif volatility < 0.005:  # Low volatility
            self.adaptation_factors['volatility'] = 1.3  # Less sensitive
        else:
            self.adaptation_factors['volatility'] = 1.0
        
        # Volume adaptation
        volume_profile = self.calculate_volume_profile()
        if volume_profile['volume_ratio'] > 1.5:  # High volume
            self.adaptation_factors['volume'] = 0.8  # More sensitive
        elif volume_profile['volume_ratio'] < 0.5:  # Low volume
            self.adaptation_factors['volume'] = 1.4  # Less sensitive
        else:
            self.adaptation_factors['volume'] = 1.0
        
        # Order flow adaptation
        order_flow = self.calculate_order_flow_metrics()
        if order_flow['delta_volatility'] > 0.2:  # High order flow volatility
            self.adaptation_factors['order_flow'] = 0.8  # More sensitive
        else:
            self.adaptation_factors['order_flow'] = 1.0
        
        # Market regime adaptation
        regime_multiplier = self.regime_thresholds.get(regime, {}).get('multiplier', 1.0)
        self.adaptation_factors['market_regime'] = regime_multiplier
        
        # Session adaptation
        session_multiplier = self.session_multipliers.get(session, 1.0)
        self.adaptation_factors['session'] = session_multiplier
        
        # Time adaptation
        self.adaptation_factors['time_of_day'] = time_mult
        
        logger.debug(f"Adaptation Factors: {self.adaptation_factors}")
        logger.debug(f"Market Regime: {regime}, Session: {session}")
    
    def update_thresholds(self):
        """Update all thresholds based on current market conditions"""
        self.calculate_adaptation_factors()
        
        # Calculate overall adaptation factor
        overall_factor = np.mean(list(self.adaptation_factors.values()))
        
        # Update each threshold
        for threshold_name, base_value in self.base_thresholds.items():
            # Apply adaptation factor
            adapted_value = base_value * overall_factor
            
            # Apply specific adjustments based on threshold type
            if 'sensitivity' in threshold_name:
                # Sensitivity thresholds: lower = more sensitive
                adapted_value = adapted_value * self.adaptation_factors['volatility']
            elif 'confidence' in threshold_name:
                # Confidence thresholds: higher = more strict
                adapted_value = adapted_value / self.adaptation_factors['market_regime']
            elif 'multiplier' in threshold_name:
                # Multiplier thresholds: adjust based on volume
                adapted_value = adapted_value * self.adaptation_factors['volume']
            elif 'threshold' in threshold_name:
                # General thresholds: adjust based on volatility
                adapted_value = adapted_value * self.adaptation_factors['volatility']
            
            # Ensure thresholds stay within reasonable bounds
            adapted_value = self.clamp_threshold(threshold_name, adapted_value)
            
            self.current_thresholds[threshold_name] = adapted_value
        
        logger.info(f"Updated Thresholds: {self.current_thresholds}")
    
    def clamp_threshold(self, threshold_name, value):
        """Clamp threshold values to reasonable bounds"""
        bounds = {
            'layering_sensitivity': (0.1, 0.5),
            'iceberg_consistency': (0.5, 0.95),
            'liquidity_multiplier': (1.5, 5.0),
            'momentum_threshold': (0.2, 1.0),
            'volume_ratio': (1.0, 3.0),
            'price_change_threshold': (0.001, 0.01),
            'order_flow_delta_threshold': (0.05, 0.3),
            'pattern_confidence_min': (1, 5),
            'signal_confidence_min': (2, 6)
        }
        
        min_val, max_val = bounds.get(threshold_name, (0, float('inf')))
        return np.clip(value, min_val, max_val)
    
    def get_threshold(self, threshold_name):
        """Get current value of a specific threshold"""
        return self.current_thresholds.get(threshold_name, self.base_thresholds.get(threshold_name))
    
    def get_all_thresholds(self):
        """Get all current thresholds"""
        return self.current_thresholds.copy()
    
    def reset_thresholds(self):
        """Reset thresholds to base values"""
        self.current_thresholds = self.base_thresholds.copy()
        logger.info("Thresholds reset to base values")
    
    def get_market_summary(self):
        """Get comprehensive market summary"""
        volatility = self.calculate_volatility()
        volume_profile = self.calculate_volume_profile()
        order_flow = self.calculate_order_flow_metrics()
        regime = self.detect_market_regime()
        session = self.get_current_session()
        
        return {
            'volatility': volatility,
            'volume_ratio': volume_profile['volume_ratio'],
            'order_flow_delta': order_flow['avg_delta'],
            'market_regime': regime,
            'session': session,
            'adaptation_factors': self.adaptation_factors.copy(),
            'thresholds': self.current_thresholds.copy()
        }

# Usage example
def test_dynamic_thresholds():
    """Test the dynamic thresholds system"""
    dt = DynamicThresholds()
    
    # Simulate market data updates
    for i in range(50):
        price = 100 + np.random.normal(0, 0.5)  # Simulate price movement
        volume = 1000 + np.random.normal(0, 200)  # Simulate volume
        order_flow = np.random.normal(0, 0.1)  # Simulate order flow
        
        # Update market data
        dt.update_market_data(price, volume, order_flow)
        
        # Update thresholds every 10 iterations
        if i % 10 == 0:
            dt.update_thresholds()
            
            # Get market summary
            summary = dt.get_market_summary()
            print(f"Iteration {i}:")
            print(f"  Volatility: {summary['volatility']:.4f}")
            print(f"  Volume Ratio: {summary['volume_ratio']:.2f}")
            print(f"  Market Regime: {summary['market_regime']}")
            print(f"  Layering Sensitivity: {summary['thresholds']['layering_sensitivity']:.3f}")
            print(f"  Signal Confidence Min: {summary['thresholds']['signal_confidence_min']:.1f}")
            print()

if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    
    # Test the system
    test_dynamic_thresholds() 
