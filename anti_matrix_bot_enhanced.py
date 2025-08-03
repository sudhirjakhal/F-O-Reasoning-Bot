#!/usr/bin/env python3
"""
Enhanced Anti-Matrix Trading Bot with Real-Time Order Book Streaming
Real-time order flow analysis for live market trading
"""
import time
import json
import logging
import datetime
import pandas as pd
import numpy as np
import websocket
import threading
import pytz
from binance.client import Client
from orderbook_analysis_enhanced import EnhancedAntiMatrixOrderBook
from dynamic_thresholds import DynamicThresholds
from dotenv import load_dotenv
import os
from telegram_bot import TelegramChannelBot

load_dotenv()

# --- CONFIG ---
API_KEY = os.getenv("BINANCE_API_KEY")
API_SECRET = os.getenv("BINANCE_API_SECRET")

#=== LOGGING SETUP ===
logging.basicConfig(
    level=logging.DEBUG,  # Changed to DEBUG for detailed logging
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('anti_matrix_bot_enhanced.log'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

#=== CONFIGURATION ===
symbol = "dogeusdt"
interval = "5m"
lookback = 150
initial_equity = 100
risk_per_trade = 0.1  # 1% risk per trade
leverage = 5  # F&O leverage (5x)
margin_requirement = 0.1  # 10% margin requirement
timezone = pytz.timezone("Asia/Kolkata")
max_position_value = initial_equity * 0.1  # 10% of initial equity

#=== GLOBAL VARIABLES ===
candles = []
candles_dataframe = None
equity = initial_equity
current_position = None
session_active = True
trades = []  # Track all trades

#=== SMC COMPONENTS ===
liquidity_levels = {'highs': [], 'lows': []}
fair_value_gaps = []
order_blocks = []

#=== STRATEGIC TRADING FRAMEWORK ===
signal_history = []
last_trade_time = None
min_trade_interval = 60
strategy_start_time = None
strategy_min_duration = 600
min_signal_consistency = 1
signal_consistency_window = 300
max_positions_per_day = 100
daily_trades = 0
last_reset_date = None
current_strategy = None
strategy_confidence = 0
strategy_signals = []

#=== REAL-TIME ORDER BOOK STREAMING ===
order_book_buffer = []
order_book_streaming = False
order_book_ws = None
order_book_thread = None
real_time_patterns = []
order_flow_history = []

#=== ENHANCED ORDER BOOK ANALYSIS INTEGRATION ===
# Initialize the enhanced AntiMatrixOrderBook from orderbook_analysis_enhanced.py
client = Client(API_KEY, API_SECRET)
ob_analyzer = EnhancedAntiMatrixOrderBook(symbol, depth=20)

#=== TELEGRAM BOT INTEGRATION ===
TELEGRAM_BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
TELEGRAM_DOGE_CHANNEL_ID = os.getenv('TELEGRAM_DOGE_CHANNEL_ID')

# Initialize telegram bot
telegram_bot = None
if TELEGRAM_BOT_TOKEN and TELEGRAM_DOGE_CHANNEL_ID:
    try:
        telegram_bot = TelegramChannelBot(TELEGRAM_BOT_TOKEN, TELEGRAM_DOGE_CHANNEL_ID)
        logger.info("✅ Telegram bot initialized successfully")
    except Exception as e:
        logger.error(f"❌ Failed to initialize telegram bot: {e}")
        telegram_bot = None
else:
    logger.warning("⚠️ Telegram credentials not found - notifications disabled")

#=== TELEGRAM NOTIFICATION FUNCTION ===
def send_telegram_message(message):
    """Send message to telegram channel"""
    global telegram_bot
    if telegram_bot:
        try:
            telegram_bot.send_message(message)
            logger.debug(f"📱 Telegram message sent: {message[:100]}...")
        except Exception as e:
            logger.error(f"❌ Failed to send telegram message: {e}")

#=== STRATEGIC TRADING FUNCTIONS ===
def check_trade_timing():
    """Check if enough time has passed since last trade"""
    global last_trade_time
    if last_trade_time is None:
        return True
    
    time_since_last = time.time() - last_trade_time
    return time_since_last >= min_trade_interval

def check_daily_limits():
    """Check daily trade limits"""
    global daily_trades, last_reset_date
    current_date = datetime.datetime.now().date()
    
    # Reset daily counter if new day
    if last_reset_date != current_date:
        daily_trades = 0
        last_reset_date = current_date
    
    return daily_trades < max_positions_per_day

def check_signal_stability(current_signal):
    """Check if signal has been stable for required duration"""
    global signal_history
    
    if current_signal == "WAIT":
        return False
    
    current_time = time.time()
    
    # Add current signal to history
    signal_history.append({
        'signal': current_signal,
        'time': current_time
    })
    
    # Remove old signals
    signal_history = [
        s for s in signal_history 
        if current_time - s['time'] <= signal_consistency_window
    ]
    
    # Check for consistency
    if len(signal_history) < min_signal_consistency:
        return False
    
    # Check if signals are consistent
    recent_signals = [s['signal'] for s in signal_history[-min_signal_consistency:]]
    return len(set(recent_signals)) == 1 and recent_signals[0] != "WAIT"

def check_strategy_consistency(current_signal, current_confidence):
    """Check if current signal aligns with ongoing strategy"""
    global current_strategy, strategy_start_time, strategy_confidence, strategy_signals
    
    current_time = time.time()
    
    # If no current strategy, start one
    if current_strategy is None:
        current_strategy = current_signal
        strategy_start_time = current_time
        strategy_confidence = current_confidence
        strategy_signals = [{
            'signal': current_signal,
            'confidence': current_confidence,
            'time': current_time
        }]
        logger.info(f"🔄 New strategy started: {current_signal} (confidence: {current_confidence})")
        return True  # Allow immediate trading for new strategies
    
    # Check if current signal matches strategy
    if current_signal == current_strategy:
        # Update strategy confidence
        strategy_confidence = max(strategy_confidence, current_confidence)
        strategy_signals.append({
            'signal': current_signal,
            'confidence': current_confidence,
            'time': current_time
        })
        
        # Check if strategy has been active long enough
        strategy_duration = current_time - strategy_start_time
        return strategy_duration >= strategy_min_duration
        
    else:
        # Signal changed - reset strategy
        logger.info(f"🔄 Strategy reset: {current_strategy} -> {current_signal}")
        current_strategy = current_signal
        strategy_start_time = current_time
        strategy_confidence = current_confidence
        strategy_signals = [{
            'signal': current_signal,
            'confidence': current_confidence,
            'time': current_time
        }]
        return False

def get_current_dataframe():
    """Get current dataframe with all price action metrics for confirmation analysis"""
    global candles_dataframe
    
    if len(candles_dataframe) < 2:  # Need at least 2 candles for confirmation
        logger.debug("get_current_dataframe - Insufficient candles for confirmation")
        return None
    
    try:
        
        logger.debug(f"get_current_dataframe - Created dataframe with {len(candles_dataframe)} candles")
        logger.debug(f"get_current_dataframe - Latest candle: {candles_dataframe.iloc[-1]['close']:.5f}")
        
        return candles_dataframe
        
    except Exception as e:
        logger.error(f"get_current_dataframe - Error creating dataframe: {e}")
        return None

def confirm_next_candle_anti_matrix(df, signal, confidence, reasons):
    """Confirm signals with next candle analysis to avoid falling into traps"""
    
    if len(df) < 2:  # Need at least 2 candles for confirmation
        return False, "Insufficient data for confirmation"
    
    current = df.iloc[-1]
    previous = df.iloc[-2]
    pre_previous = df.iloc[-3]
    
    # TRAP CONFIRMATION LOGIC
    trap_confirmation = {
        # 'ask_layering': False,
        # 'bid_layering': False,
        'liquidity_grab': False,
        'stop_hunting': False,
        'bullish_trap': False,
        'bearish_trap': False
    }
    
    # # 1. ASK LAYERING CONFIRMATION (Anti-Matrix Logic)
    # if any('ask layering' in reason.lower() for reason in reasons):
    #     # Ask layering should lead to SHORT signal
    #     # But we need to confirm it's not a fake pump
    #     if signal == "SHORT":
    #         # Check if price actually went up (trap working)
    #         if current['high'] > previous['high']:
    #             # Price went up as expected in ask layering trap
    #             trap_confirmation['ask_layering'] = True
    #             logger.info(f"✅ ASK LAYERING CONFIRMED - Price went up as expected in trap")
    #         else:
    #             # Price didn't go up - might be fake pattern
    #             logger.warning(f"⚠️ ASK LAYERING WARNING - Price didn't move up as expected")
    #             return False, "Ask layering not confirmed"
    
    # # 2. BID LAYERING CONFIRMATION (Anti-Matrix Logic)
    # if any('bid layering' in reason.lower() for reason in reasons):
    #     # Bid layering should lead to LONG signal
    #     # But we need to confirm it's not a fake dump
    #     if signal == "LONG":
    #         # Check if price actually went down (trap working)
    #         if current['low'] < previous['low']:
    #             # Price went down as expected in bid layering trap
    #             trap_confirmation['bid_layering'] = True
    #             logger.info(f"✅ BID LAYERING CONFIRMED - Price went down as expected in trap")
    #         else:
    #             # Price didn't go down - might be fake pattern
    #             logger.warning(f"⚠️ BID LAYERING WARNING - Price didn't move down as expected")
    #             return False, "Bid layering not confirmed"
    
    # 3. LIQUIDITY GRAB CONFIRMATION
    if any('liquidity grab' in reason.lower() for reason in reasons):
        # Liquidity grab should show price movement in opposite direction
        if signal == "SHORT":
            # Should see price spike up then reverse
            if current['high'] > previous['high'] and current['close'] < previous['close']:
                trap_confirmation['liquidity_grab'] = True
                logger.info(f"✅ LIQUIDITY GRAB CONFIRMED - Price spiked then reversed")
            else:
                logger.warning(f"⚠️ LIQUIDITY GRAB WARNING - No spike and reverse pattern")
                return False, "Liquidity grab not confirmed"
    
    # 4. STOP HUNTING CONFIRMATION
    if any('stop hunting' in reason.lower() for reason in reasons):
        # Stop hunting should show price moving beyond key levels then reversing
        if signal == "LONG":
            # Should see price break below support then bounce
            if current['low'] < previous['low'] and current['close'] > previous['close']:
                trap_confirmation['stop_hunting'] = True
                logger.info(f"✅ STOP HUNTING CONFIRMED - Price broke support then bounced")
            else:
                logger.warning(f"⚠️ STOP HUNTING WARNING - No break and bounce pattern")
                return False, "Stop hunting not confirmed"
    
    # 5. BULLISH/BEARISH TRAP CONFIRMATION
    if any('bullish trap' in reason.lower() for reason in reasons):
        # Bullish trap should show price going up then reversing
        if signal == "SHORT":
            if current['high'] > previous['high'] and current['close'] < previous['close']:
                trap_confirmation['bullish_trap'] = True
                logger.info(f"✅ BULLISH TRAP CONFIRMED - Price went up then reversed")
            else:
                logger.warning(f"⚠️ BULLISH TRAP WARNING - No up then reverse pattern")
                return False, "Bullish trap not confirmed"
    
    if any('bearish trap' in reason.lower() for reason in reasons):
        # Bearish trap should show price going down then reversing
        if signal == "LONG":
            if current['low'] < previous['low'] and current['close'] > previous['close']:
                trap_confirmation['bearish_trap'] = True
                logger.info(f"✅ BEARISH TRAP CONFIRMED - Price went down then reversed")
            else:
                logger.warning(f"⚠️ BEARISH TRAP WARNING - No down then reverse pattern")
                return False, "Bearish trap not confirmed"
    
    # 6. VOLUME CONFIRMATION
    volume_confirmed = False
    if any('volume:' in reason.lower() for reason in reasons):
        avg_volume = df['volume'].rolling(window=10).mean().iloc[-1]
        if current['volume'] > avg_volume * 1.2:
            volume_confirmed = True
            logger.info(f"✅ VOLUME CONFIRMED - High volume supports pattern")
        else:
            logger.warning(f"⚠️ VOLUME WARNING - Low volume doesn't support pattern")
            return False, "Volume not confirmed"
    
    # 7. MOMENTUM CONFIRMATION
    momentum_confirmed = False
    if signal == "LONG":
        if current['close'] > previous['close'] and current['close'] > current['open']:
            momentum_confirmed = True
            logger.info(f"✅ MOMENTUM CONFIRMED - Price moving up as expected")
    elif signal == "SHORT":
        if current['close'] < previous['close'] and current['close'] < current['open']:
            momentum_confirmed = True
            logger.info(f"✅ MOMENTUM CONFIRMED - Price moving down as expected")
    
    # 8. FINAL CONFIRMATION DECISION
    confirmed_patterns = sum(trap_confirmation.values())
    
    if confirmed_patterns > 0 and momentum_confirmed:
        logger.info(f"✅ NEXT CANDLE CONFIRMATION PASSED - {confirmed_patterns} patterns confirmed")
        return True, f"{confirmed_patterns} patterns confirmed with momentum"
    elif confirmed_patterns > 0:
        logger.warning(f"⚠️ PARTIAL CONFIRMATION - Patterns confirmed but momentum unclear")
        return True, f"{confirmed_patterns} patterns confirmed but momentum unclear"
    else:
        logger.error(f"❌ NO CONFIRMATION - Patterns not playing out as expected")
        return False, "No patterns confirmed"

def should_take_trade(current_signal, current_confidence, current_reasons):
    """Determine if we should take a trade based on strategic framework"""
    
    # 1. CHECK TRADE TIMING
    if not check_trade_timing():
        logger.debug("❌ Trade timing check failed - too soon since last trade")
        return False, "Trade timing"
    
    # 2. CHECK DAILY LIMITS
    if not check_daily_limits():
        logger.debug("❌ Daily trade limit reached")
        return False, "Daily limit"
    
    # 3. CHECK SIGNAL STABILITY
    if not check_signal_stability(current_signal):
        logger.debug("❌ Signal not stable enough")
        return False, "Signal stability"
    
    # 4. CHECK STRATEGY CONSISTENCY
    if not check_strategy_consistency(current_signal, current_confidence):
        logger.debug("❌ Strategy not consistent")
        return False, "Strategy consistency"
    
    # 5. ENHANCED CONFIDENCE THRESHOLD
    base_threshold = 3  # INCREASED from 1 to 3 for better quality trades
    if strategy_confidence > 5:
        base_threshold += 1
    
    if current_confidence < base_threshold:
        logger.debug(f"❌ Confidence too low: {current_confidence} < {base_threshold}")
        return False, "Confidence threshold"
    
    # 6. ENHANCED: Check for quality signals (not just volume)
    if current_reasons:
        volume_only_signals = [reason for reason in current_reasons if 'volume:' in str(reason).lower()]
        other_signals = [reason for reason in current_reasons if 'volume:' not in str(reason).lower()]
        
        # Don't take trades with only volume signals
        if len(volume_only_signals) > 0 and len(other_signals) == 0:
            logger.debug("❌ Only volume signals detected - insufficient for trade")
            return False, "Volume only signals"
        
        # Require at least one strong signal (trap, structure break, order block, FVG, order book bias)
        strong_signals = [reason for reason in other_signals if any(keyword in str(reason).lower() 
                       for keyword in ['trap', 'bos', 'choch', 'order block', 'fvg filled', 
                                      'bearish bias', 'bullish bias', 'liquidity grab', 
                                      'smart money', 'distribution regime', 'accumulation regime'])]
        
        if len(strong_signals) == 0:
            logger.debug("❌ No strong signals detected - insufficient for trade")
            return False, "No strong signals"
    
    # 7. NEW: NEXT CANDLE CONFIRMATION (ANTI-MATRIX TRAP AVOIDANCE)
    if current_signal in ["LONG", "SHORT"]:
        # Get current dataframe for confirmation
        df = get_current_dataframe()  # You'll need to implement this
        if df is not None and len(df) >= 3:
            confirmed, confirmation_reason = confirm_next_candle_anti_matrix(df, current_signal, current_confidence, current_reasons)
            if not confirmed:
                logger.debug(f"❌ Next candle confirmation failed: {confirmation_reason}")
                return False, f"Next candle confirmation: {confirmation_reason}"
            else:
                logger.info(f"✅ Next candle confirmation passed: {confirmation_reason}")
        else:
            logger.warning("⚠️ Cannot confirm next candle - insufficient data")
            return False, "Cannot confirm next candle"
    
    return True, "All checks passed including next candle confirmation"

def record_trade():
    """Record that a trade was taken"""
    global last_trade_time, daily_trades
    last_trade_time = time.time()
    daily_trades += 1
    logger.info(f"📊 Trade recorded. Daily trades: {daily_trades}/{max_positions_per_day}")

def check_real_time_exit_conditions(current_price):
    """Check exit conditions in real-time for F&O trading"""
    global current_position, equity
    
    if not current_position:
        return
    
    logger.debug(f"🔍 REAL-TIME EXIT CHECK - Price: {current_price:.5f}, Position: {current_position['signal']}")
    
    if current_position['signal'] == "LONG":
        # Check for stop loss
        if current_price <= current_position['stop_loss']:
            pnl = (current_position['stop_loss'] - current_position['entry']) * current_position['position_size']
            equity += pnl
            logger.info(f"🛑 REAL-TIME STOP LOSS: PnL = ${pnl:.2f} | Equity = ${equity:.2f}")
            logger.info(f"📊 Exit Details - Entry: {current_position['entry']:.5f}, Exit: {current_position['stop_loss']:.5f}, Price Movement: {((current_position['stop_loss']/current_position['entry'])-1)*100:.2f}%")
            current_position = None
            return
        
        # Check for take profit
        elif current_price >= current_position['take_profit']:
            pnl = (current_position['take_profit'] - current_position['entry']) * current_position['position_size']
            equity += pnl
            logger.info(f"🎉 REAL-TIME TAKE PROFIT: PnL = ${pnl:.2f} | Equity = ${equity:.2f}")
            logger.info(f"📊 Exit Details - Entry: {current_position['entry']:.5f}, Exit: {current_position['take_profit']:.5f}, Price Movement: {((current_position['take_profit']/current_position['entry'])-1)*100:.2f}%")
            current_position = None
            return
        
        # Log current PnL
        current_pnl = (current_price - current_position['entry']) * current_position['position_size']
        pnl_percentage = (current_pnl / (equity * risk_per_trade)) * 100
        logger.debug(f"📈 Current PnL: ${current_pnl:.2f} ({pnl_percentage:.1f}% of risk)")
        
        # DYNAMIC TP ADJUSTMENT FOR F&O (increase TP if strong momentum continues)
        price_profit_percentage = (current_price / current_position['entry'] - 1) * 100
        
        # If we're at 80% of original TP and momentum is strong, extend TP
        original_tp_distance = current_position['take_profit'] - current_position['entry']
        current_distance = current_price - current_position['entry']
        
        if current_distance >= original_tp_distance * 0.8 and price_profit_percentage >= 8:
            # Extend TP by 50% if we're close to original TP and momentum is strong
            new_tp = current_position['take_profit'] + (original_tp_distance * 0.5)
            current_position['take_profit'] = new_tp
            logger.info(f"🚀 EXTENDED TP: Moved TP to {new_tp:.5f} (strong momentum detected)")
        
        # ENHANCED TRAILING STOP LOSS FOR F&O (LONG) - USING RISK-BASED PERCENTAGE
        # Use risk-based percentage for trailing stop decisions
        risk_percentage = pnl_percentage  # Already calculated above
        
        logger.debug(f"Trailing Stop Debug (LONG) - Entry: {current_position['entry']:.5f}, Current Price: {current_price:.5f}")
        logger.debug(f"Trailing Stop Debug (LONG) - Risk Profit: {risk_percentage:.2f}%, Price Profit: {price_profit_percentage:.2f}%")
        
        if risk_percentage >= 5:  # 5% of risk - move SL to 2% profit
            new_stop_loss = current_position['entry'] * 1.02
            if new_stop_loss > current_position['stop_loss']:
                current_position['stop_loss'] = new_stop_loss
                logger.info(f"🔄 TRAILING STOP: Moved SL to {new_stop_loss:.5f} (2% profit)")
                logger.debug(f"Trailing Stop Debug (LONG) - Current SL: {current_position['stop_loss']:.5f}, New SL: {new_stop_loss:.5f}")
                
                # Send telegram notification for trailing stop
                telegram_msg = f"""
🔄 *TRAILING STOP ACTIVATED*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*New Stop Loss:* {new_stop_loss:.5f} (2% profit)
*Risk Profit:* {risk_percentage:.2f}%
                """
                send_telegram_message(telegram_msg)
        
        elif risk_percentage >= 3:  # 3% of risk - move SL to 1% profit
            new_stop_loss = current_position['entry'] * 1.01
            if new_stop_loss > current_position['stop_loss']:
                current_position['stop_loss'] = new_stop_loss
                logger.info(f"🔄 TRAILING STOP: Moved SL to {new_stop_loss:.5f} (1% profit)")
                logger.debug(f"Trailing Stop Debug (LONG) - Current SL: {current_position['stop_loss']:.5f}, New SL: {new_stop_loss:.5f}")
                
                # Send telegram notification for trailing stop
                telegram_msg = f"""
🔄 *TRAILING STOP ACTIVATED*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*New Stop Loss:* {new_stop_loss:.5f} (1% profit)
*Risk Profit:* {risk_percentage:.2f}%
                """
                send_telegram_message(telegram_msg)
        
        elif risk_percentage >= 1:  # 1% of risk - move SL to breakeven
            new_stop_loss = current_position['entry']
            if new_stop_loss > current_position['stop_loss']:
                current_position['stop_loss'] = new_stop_loss
                logger.info(f"🔄 TRAILING STOP: Moved SL to {new_stop_loss:.5f} (breakeven)")
                logger.debug(f"Trailing Stop Debug (LONG) - Current SL: {current_position['stop_loss']:.5f}, New SL: {new_stop_loss:.5f}")
                
                # Send telegram notification for trailing stop
                telegram_msg = f"""
🔄 *TRAILING STOP ACTIVATED*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*New Stop Loss:* {new_stop_loss:.5f} (breakeven)
*Risk Profit:* {risk_percentage:.2f}%
                """
                send_telegram_message(telegram_msg)
    
    else:  # SHORT
        # Check for stop loss
        if current_price >= current_position['stop_loss']:
            pnl = (current_position['entry'] - current_position['stop_loss']) * current_position['position_size']
            equity += pnl
            logger.info(f"🛑 REAL-TIME STOP LOSS: PnL = ${pnl:.2f} | Equity = ${equity:.2f}")
            logger.info(f"📊 Exit Details - Entry: {current_position['entry']:.5f}, Exit: {current_position['stop_loss']:.5f}, Price Movement: {((current_position['entry']/current_position['stop_loss'])-1)*100:.2f}%")
            current_position = None
            return
        
        # Check for take profit
        elif current_price <= current_position['take_profit']:
            pnl = (current_position['entry'] - current_position['take_profit']) * current_position['position_size']
            equity += pnl
            logger.info(f"🎉 REAL-TIME TAKE PROFIT: PnL = ${pnl:.2f} | Equity = ${equity:.2f}")
            logger.info(f"📊 Exit Details - Entry: {current_position['entry']:.5f}, Exit: {current_position['take_profit']:.5f}, Price Movement: {((current_position['entry']/current_position['take_profit'])-1)*100:.2f}%")
            current_position = None
            return
        
        # Log current PnL
        current_pnl = (current_position['entry'] - current_price) * current_position['position_size']
        pnl_percentage = (current_pnl / (equity * risk_per_trade)) * 100
        logger.debug(f"📈 Current PnL: ${current_pnl:.2f} ({pnl_percentage:.1f}% of risk)")
        
        # DYNAMIC TP ADJUSTMENT FOR F&O (SHORT) (increase TP if strong momentum continues)
        price_profit_percentage = (current_position['entry'] / current_price - 1) * 100
        
        # If we're at 80% of original TP and momentum is strong, extend TP
        original_tp_distance = current_position['entry'] - current_position['take_profit']
        current_distance = current_position['entry'] - current_price
        
        if current_distance >= original_tp_distance * 0.8 and price_profit_percentage >= 8:
            # Extend TP by 50% if we're close to original TP and momentum is strong
            new_tp = current_position['take_profit'] - (original_tp_distance * 0.5)
            current_position['take_profit'] = new_tp
            logger.info(f"🚀 EXTENDED TP: Moved TP to {new_tp:.5f} (strong momentum detected)")
        
        # ENHANCED TRAILING STOP LOSS FOR F&O (SHORT) - USING RISK-BASED PERCENTAGE
        # Use risk-based percentage for trailing stop decisions
        risk_percentage = pnl_percentage  # Already calculated above
        
        logger.debug(f"Trailing Stop Debug (SHORT) - Entry: {current_position['entry']:.5f}, Current Price: {current_price:.5f}")
        logger.debug(f"Trailing Stop Debug (SHORT) - Risk Profit: {risk_percentage:.2f}%, Price Profit: {price_profit_percentage:.2f}%")
        
        if risk_percentage >= 5:  # 5% of risk - move SL to 2% profit
            new_stop_loss = current_position['entry'] * 0.98
            if new_stop_loss < current_position['stop_loss']:
                current_position['stop_loss'] = new_stop_loss
                logger.info(f"🔄 TRAILING STOP: Moved SL to {new_stop_loss:.5f} (2% profit)")
                logger.debug(f"Trailing Stop Debug (SHORT) - Current SL: {current_position['stop_loss']:.5f}, New SL: {new_stop_loss:.5f}")
                
                # Send telegram notification for trailing stop
                telegram_msg = f"""
🔄 *TRAILING STOP ACTIVATED*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*New Stop Loss:* {new_stop_loss:.5f} (2% profit)
*Risk Profit:* {risk_percentage:.2f}%
                """
                send_telegram_message(telegram_msg)
        
        elif risk_percentage >= 3:  # 3% of risk - move SL to 1% profit
            new_stop_loss = current_position['entry'] * 0.99
            if new_stop_loss < current_position['stop_loss']:
                current_position['stop_loss'] = new_stop_loss
                logger.info(f"🔄 TRAILING STOP: Moved SL to {new_stop_loss:.5f} (1% profit)")
                logger.debug(f"Trailing Stop Debug (SHORT) - Current SL: {current_position['stop_loss']:.5f}, New SL: {new_stop_loss:.5f}")
                
                # Send telegram notification for trailing stop
                telegram_msg = f"""
🔄 *TRAILING STOP ACTIVATED*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*New Stop Loss:* {new_stop_loss:.5f} (1% profit)
*Risk Profit:* {risk_percentage:.2f}%
                """
                send_telegram_message(telegram_msg)
        
        elif risk_percentage >= 1:  # 1% of risk - move SL to breakeven
            new_stop_loss = current_position['entry']
            if new_stop_loss < current_position['stop_loss']:
                current_position['stop_loss'] = new_stop_loss
                logger.info(f"🔄 TRAILING STOP: Moved SL to {new_stop_loss:.5f} (breakeven)")
                logger.debug(f"Trailing Stop Debug (SHORT) - Current SL: {current_position['stop_loss']:.5f}, New SL: {new_stop_loss:.5f}")
                
                # Send telegram notification for trailing stop
                telegram_msg = f"""
🔄 *TRAILING STOP ACTIVATED*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*New Stop Loss:* {new_stop_loss:.5f} (breakeven)
*Risk Profit:* {risk_percentage:.2f}%
                """
                send_telegram_message(telegram_msg)

#=== SESSION MANAGEMENT ===
def is_trading_session():
    """Allow trading during all sessions for better performance"""
    return True  # Always allow trading for better performance based on backtest results

#=== REAL-TIME LIQUIDITY TRACKING ===
def update_liquidity_levels(df):
    """Track recent highs/lows for liquidity sweeps"""
    global liquidity_levels
    
    if len(df) < 20:  # Increased from 10 for robustness
        logger.debug("Liquidity tracking - Insufficient data")
        return
    
    # Update recent highs and lows
    recent_highs = df['high'].rolling(window=10).max().dropna()
    recent_lows = df['low'].rolling(window=10).min().dropna()
    
    latest = df.iloc[-1]
    prev = df.iloc[-2]
    
    logger.debug(f"Liquidity Analysis - Current: {latest['high']:.5f}/{latest['low']:.5f}, Prev: {prev['high']:.5f}/{prev['low']:.5f}")
    
    # Detect liquidity sweeps
    if latest['high'] > prev['high'] and latest['close'] < prev['close']:
        # Bearish liquidity sweep
        liquidity_levels['highs'].append({
            'price': latest['high'],
            'time': latest.name,
            'swept': True
        })
        logger.info(f"🔄 Bearish Liquidity Sweep at {latest['high']:.5f}")
        logger.debug(f"Liquidity Sweep Details - High: {latest['high']:.5f}, Close: {latest['close']:.5f}, Prev Close: {prev['close']:.5f}")
    
    if latest['low'] < prev['low'] and latest['close'] > prev['close']:
        # Bullish liquidity sweep
        liquidity_levels['lows'].append({
            'price': latest['low'],
            'time': latest.name,
            'swept': True
        })
        logger.info(f"🔄 Bullish Liquidity Sweep at {latest['low']:.5f}")
        logger.debug(f"Liquidity Sweep Details - Low: {latest['low']:.5f}, Close: {latest['close']:.5f}, Prev Close: {prev['close']:.5f}")
    
    # Clean old levels (keep last 20)
    liquidity_levels['highs'] = liquidity_levels['highs'][-20:]
    liquidity_levels['lows'] = liquidity_levels['lows'][-20:]
    
    logger.debug(f"Liquidity Levels - Highs: {len(liquidity_levels['highs'])}, Lows: {len(liquidity_levels['lows'])}")

#=== FAIR VALUE GAP DETECTION ===
def detect_fair_value_gaps(df):
    """Detect 3-candle Fair Value Gaps"""
    global fair_value_gaps
    
    if len(df) < 3:
        logger.debug("FVG detection - Insufficient data")
        return
    
    current = df.iloc[-1]
    prev = df.iloc[-2]
    prev2 = df.iloc[-3]
    
    logger.debug(f"FVG Analysis - Current: {current['low']:.5f}/{current['high']:.5f}, Prev: {prev['low']:.5f}/{prev['high']:.5f}")
    
    # Bullish FVG: Current low > Previous high
    if current['low'] > prev['high']:
        fvg = {
            'type': 'bullish',
            'start': prev['high'],
            'end': current['low'],
            'time': current.name,
            'filled': False
        }
        fair_value_gaps.append(fvg)
        logger.info(f"📈 Bullish FVG: {prev['high']:.5f} - {current['low']:.5f}")
        logger.debug(f"FVG Details - Gap size: {current['low'] - prev['high']:.5f}")
    
    # Bearish FVG: Current high < Previous low
    elif current['high'] < prev['low']:
        fvg = {
            'type': 'bearish',
            'start': current['high'],
            'end': prev['low'],
            'time': current.name,
            'filled': False
        }
        fair_value_gaps.append(fvg)
        logger.info(f"📉 Bearish FVG: {current['high']:.5f} - {prev['low']:.5f}")
        logger.debug(f"FVG Details - Gap size: {prev['low'] - current['high']:.5f}")
    
    # Check if FVGs are filled
    for fvg in fair_value_gaps:
        if not fvg['filled']:
            if fvg['type'] == 'bullish' and current['low'] <= fvg['start']:
                fvg['filled'] = True
                logger.info(f"✅ Bullish FVG Filled at {current['low']:.5f}")
                logger.debug(f"FVG Fill Details - Fill price: {current['low']:.5f}, FVG start: {fvg['start']:.5f}")
            elif fvg['type'] == 'bearish' and current['high'] >= fvg['end']:
                fvg['filled'] = True
                logger.info(f"✅ Bearish FVG Filled at {current['high']:.5f}")
                logger.debug(f"FVG Fill Details - Fill price: {current['high']:.5f}, FVG end: {fvg['end']:.5f}")
    
    # Clean old FVGs
    fair_value_gaps = [fvg for fvg in fair_value_gaps if not fvg['filled'] or (current.name - fvg['time']).total_seconds() < 3600]
    logger.debug(f"Active FVGs: {len(fair_value_gaps)}")

#=== ORDER BLOCK DETECTION ===
def detect_order_blocks(df):
    """Detect institutional order blocks"""
    global order_blocks
    
    if len(df) < 5:  # Increased from 3 for robustness
        logger.debug("Order block detection - Insufficient data")
        return
    
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    logger.debug(f"Order Block Analysis - Prev body: {prev['body']:.5f}, range: {prev['range']:.5f}, ratio: {prev['body']/prev['range']:.3f}")
    
    # Bullish Order Block: Strong bullish candle followed by bearish move
    if (prev['close'] > prev['open'] and 
        prev['body'] > prev['range'] * 0.6 and
        current['close'] < current['open']):
        
        ob = {
            'type': 'bullish',
            'high': prev['high'],
            'low': prev['low'],
            'time': prev.name,
            'active': True
        }
        order_blocks.append(ob)
        logger.info(f"📦 Bullish Order Block: {prev['low']:.5f} - {prev['high']:.5f}")
        logger.debug(f"Order Block Details - Body ratio: {prev['body']/prev['range']:.3f}, Volume: {prev['volume']:.2f}")
    
    # Bearish Order Block: Strong bearish candle followed by bullish move
    elif (prev['close'] < prev['open'] and 
          prev['body'] > prev['range'] * 0.6 and
          current['close'] > current['open']):
        
        ob = {
            'type': 'bearish',
            'high': prev['high'],
            'low': prev['low'],
            'time': prev.name,
            'active': True
        }
        order_blocks.append(ob)
        logger.info(f"📦 Bearish Order Block: {prev['low']:.5f} - {prev['high']:.5f}")
        logger.debug(f"Order Block Details - Body ratio: {prev['body']/prev['range']:.3f}, Volume: {prev['volume']:.2f}")
    
    # Check if order blocks are hit
    for ob in order_blocks:
        if ob['active']:
            if ob['type'] == 'bullish' and current['low'] <= ob['high']:
                ob['active'] = False
                logger.info(f"🎯 Bullish Order Block Hit at {current['low']:.5f}")
                logger.debug(f"Order Block Hit Details - Hit price: {current['low']:.5f}, Block high: {ob['high']:.5f}")
            elif ob['type'] == 'bearish' and current['high'] >= ob['low']:
                ob['active'] = False
                logger.info(f"🎯 Bearish Order Block Hit at {current['high']:.5f}")
                logger.debug(f"Order Block Hit Details - Hit price: {current['high']:.5f}, Block low: {ob['low']:.5f}")
    
    # Clean old order blocks
    order_blocks = [ob for ob in order_blocks if ob['active'] or (current.name - ob['time']).total_seconds() < 7200]
    logger.debug(f"Active Order Blocks: {len(order_blocks)}")

#=== TRAP CANDLE DETECTION ===
def detect_trap_candles(df):
    """Detect institutional trap candles"""
    if len(df) < 2:  # Increased from 1 for robustness
        logger.debug("Trap candle detection - Insufficient data")
        return None
    
    current = df.iloc[-1]
    avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
    volume_ratio = current['volume'] / avg_volume
    
    logger.debug(f"Trap Analysis - Upper wick: {current['upper_wick']:.5f}, Lower wick: {current['lower_wick']:.5f}, Body: {current['body']:.5f}")
    logger.debug(f"Trap Analysis - Volume ratio: {volume_ratio:.2f}, Close vs Open: {current['close']:.5f} vs {current['open']:.5f}")
    
    # Bullish Trap: Long upper wick, bearish close
    if (current['upper_wick'] > current['body'] * 1.5 and  # Reduced from 2 to 1.5
        current['close'] < current['open'] and
        current['volume'] > avg_volume * 1.2):  # Reduced from 1.5 to 1.2
        trap = {
            'type': 'bearish_trap',
            'price': current['high'],
            'strength': current['upper_wick'] / current['body']
        }
        logger.info(f"🕯 Bearish Trap Detected at {current['high']:.5f}")
        logger.debug(f"Trap Details - Wick ratio: {current['upper_wick']/current['body']:.2f}, Volume ratio: {volume_ratio:.2f}")
        return trap
    
    # Bearish Trap: Long lower wick, bullish close
    elif (current['lower_wick'] > current['body'] * 1.5 and  # Reduced from 2 to 1.5
          current['close'] > current['open'] and
          current['volume'] > avg_volume * 1.2):  # Reduced from 1.5 to 1.2
        trap = {
            'type': 'bullish_trap',
            'price': current['low'],
            'strength': current['lower_wick'] / current['body']
        }
        logger.info(f"🕯 Bullish Trap Detected at {current['low']:.5f}")
        logger.debug(f"Trap Details - Wick ratio: {current['lower_wick']/current['body']:.2f}, Volume ratio: {volume_ratio:.2f}")
        return trap
    
    logger.debug("No trap candle detected")
    return None

#=== BREAK OF STRUCTURE DETECTION ===
def detect_structure_breaks(df):
    """Detect Break of Structure and Change of Character"""
    if len(df) < 10:  # Increased from 5 for robustness
        logger.debug("Structure break detection - Insufficient data")
        return None
    
    # Find recent swing highs and lows
    highs = df['high'].rolling(window=5, center=True).max()
    lows = df['low'].rolling(window=5, center=True).min()
    
    current = df.iloc[-1]
    prev = df.iloc[-2]
    
    logger.debug(f"Structure Analysis - Current high: {current['high']:.5f}, Prev swing high: {highs.iloc[-2]:.5f}")
    logger.debug(f"Structure Analysis - Current low: {current['low']:.5f}, Prev swing low: {lows.iloc[-2]:.5f}")
    
    # Break of Structure (BoS)
    if current['high'] > highs.iloc[-2]:  # New high
        structure = {'type': 'BoS', 'direction': 'up', 'price': current['high']}
        logger.info(f"📈 Break of Structure UP at {current['high']:.5f}")
        logger.debug(f"BoS Details - New high: {current['high']:.5f}, Previous swing: {highs.iloc[-2]:.5f}")
        return structure
    elif current['low'] < lows.iloc[-2]:  # New low
        structure = {'type': 'BoS', 'direction': 'down', 'price': current['low']}
        logger.info(f"📉 Break of Structure DOWN at {current['low']:.5f}")
        logger.debug(f"BoS Details - New low: {current['low']:.5f}, Previous swing: {lows.iloc[-2]:.5f}")
        return structure
    
    # Change of Character (CHoCH)
    if (current['low'] < lows.iloc[-3] and current['close'] > current['open']):
        structure = {'type': 'CHoCH', 'direction': 'up', 'price': current['low']}
        logger.info(f"🔄 Change of Character UP at {current['low']:.5f}")
        logger.debug(f"CHoCH Details - Low: {current['low']:.5f}, Previous swing low: {lows.iloc[-3]:.5f}")
        return structure
    elif (current['high'] > highs.iloc[-3] and current['close'] < current['open']):
        structure = {'type': 'CHoCH', 'direction': 'down', 'price': current['high']}
        logger.info(f"🔄 Change of Character DOWN at {current['high']:.5f}")
        logger.debug(f"CHoCH Details - High: {current['high']:.5f}, Previous swing high: {highs.iloc[-3]:.5f}")
        return structure
    
    logger.debug("No structure break detected")
    return None

#=== CONTEXTUAL VOLUME ANALYSIS ===
def analyze_volume_context(df):
    """Analyze volume in context of price action and market structure"""
    if len(df) < 20:  # Increased from 10 for robustness
        logger.debug("Volume analysis - Insufficient data")
        return None
    
    current = df.iloc[-1]
    avg_volume = df['volume'].rolling(window=20).mean().iloc[-1]
    volume_ratio = current['volume'] / avg_volume
    
    logger.debug(f"Volume Analysis - Current volume: {current['volume']:.2f}, Avg volume: {avg_volume:.2f}, Ratio: {volume_ratio:.2f}")
    logger.debug(f"Volume Analysis - Close: {current['close']:.5f}, Open: {current['open']:.5f}, Bullish: {current['close'] > current['open']}")
    
    # ENHANCED: Check for market structure context
    recent_highs = df['high'].rolling(window=10).max().tail(5)
    recent_lows = df['low'].rolling(window=10).min().tail(5)
    
    # Determine if we're near key levels
    near_resistance = current['high'] >= recent_highs.max() * 0.995
    near_support = current['low'] <= recent_lows.min() * 1.005
    
    # ENHANCED: High volume with bearish close = potential distribution (with better context)
    if volume_ratio > 1.5 and current['close'] < current['open']:
        # Check if this is in a downtrend or just a pullback
        recent_trend = (df['close'].iloc[-1] - df['close'].iloc[-5]) / df['close'].iloc[-5]
        
        # ENHANCED: Check for bearish market structure
        if recent_trend < -0.02:  # Strong downtrend
            volume_ctx = {'type': 'distribution', 'strength': volume_ratio}
            logger.info(f"📊 Volume Distribution detected (ratio: {volume_ratio:.2f}) - Strong downtrend")
            logger.debug(f"Distribution Details - Volume ratio: {volume_ratio:.2f}, Bearish close, Trend: {recent_trend:.2%}")
            return volume_ctx
        elif near_resistance:  # High volume rejection at resistance
            volume_ctx = {'type': 'resistance_rejection', 'strength': volume_ratio}
            logger.info(f"📊 Resistance Rejection detected (ratio: {volume_ratio:.2f}) - High volume at resistance")
            logger.debug(f"Resistance Rejection Details - Volume ratio: {volume_ratio:.2f}, Near resistance: {near_resistance}")
            return volume_ctx
        else:
            volume_ctx = {'type': 'high_volume_bearish', 'strength': volume_ratio}
            logger.info(f"📊 High Volume Bearish detected (ratio: {volume_ratio:.2f}) - Not in downtrend")
            logger.debug(f"High Volume Bearish Details - Volume ratio: {volume_ratio:.2f}, Bearish close, Trend: {recent_trend:.2%}")
        return volume_ctx
    
    # ENHANCED: High volume with bullish close = potential accumulation (with better context)
    elif volume_ratio > 1.5 and current['close'] > current['open']:
        # Check if this is in an uptrend or just a bounce
        recent_trend = (df['close'].iloc[-1] - df['close'].iloc[-5]) / df['close'].iloc[-5]
        
        # ENHANCED: Check for bullish market structure
        if recent_trend > 0.02:  # Strong uptrend
            volume_ctx = {'type': 'accumulation', 'strength': volume_ratio}
            logger.info(f"📊 Volume Accumulation detected (ratio: {volume_ratio:.2f}) - Strong uptrend")
            logger.debug(f"Accumulation Details - Volume ratio: {volume_ratio:.2f}, Bullish close, Trend: {recent_trend:.2%}")
            return volume_ctx
        elif near_support:  # High volume bounce at support
            volume_ctx = {'type': 'support_bounce', 'strength': volume_ratio}
            logger.info(f"📊 Support Bounce detected (ratio: {volume_ratio:.2f}) - High volume at support")
            logger.debug(f"Support Bounce Details - Volume ratio: {volume_ratio:.2f}, Near support: {near_support}")
            return volume_ctx
        else:
            volume_ctx = {'type': 'high_volume_bullish', 'strength': volume_ratio}
            logger.info(f"📊 High Volume Bullish detected (ratio: {volume_ratio:.2f}) - Not in uptrend")
            logger.debug(f"High Volume Bullish Details - Volume ratio: {volume_ratio:.2f}, Bullish close, Trend: {recent_trend:.2%}")
        return volume_ctx
    
    # ENHANCED: Moderate volume with strong price action
    elif volume_ratio > 1.2 and volume_ratio <= 1.5:
        if current['close'] < current['open'] and current['body'] > current['range'] * 0.7:
            volume_ctx = {'type': 'moderate_bearish', 'strength': volume_ratio}
            logger.info(f"📊 Moderate Bearish Volume detected (ratio: {volume_ratio:.2f}) - Strong bearish candle")
            logger.debug(f"Moderate Bearish Details - Volume ratio: {volume_ratio:.2f}, Strong bearish body")
            return volume_ctx
        elif current['close'] > current['open'] and current['body'] > current['range'] * 0.7:
            volume_ctx = {'type': 'moderate_bullish', 'strength': volume_ratio}
            logger.info(f"📊 Moderate Bullish Volume detected (ratio: {volume_ratio:.2f}) - Strong bullish candle")
            logger.debug(f"Moderate Bullish Details - Volume ratio: {volume_ratio:.2f}, Strong bullish body")
            return volume_ctx
    
    # ENHANCED: Low volume = consolidation (with context)
    elif volume_ratio < 0.5:
        # Check if this is a genuine consolidation or just low activity
        price_range = (df['high'].tail(5).max() - df['low'].tail(5).min()) / df['close'].iloc[-1]
        
        if price_range < 0.01:  # Low price range = genuine consolidation
            volume_ctx = {'type': 'consolidation', 'strength': volume_ratio}
            logger.info(f"📊 Volume Consolidation detected (ratio: {volume_ratio:.2f}) - Low price range")
            logger.debug(f"Consolidation Details - Volume ratio: {volume_ratio:.2f}, Price range: {price_range:.2%}")
            return volume_ctx
        else:
            volume_ctx = {'type': 'low_activity', 'strength': volume_ratio}
            logger.info(f"📊 Low Activity detected (ratio: {volume_ratio:.2f}) - Normal price range")
            logger.debug(f"Low Activity Details - Volume ratio: {volume_ratio:.2f}, Price range: {price_range:.2%}")
        return volume_ctx
    
    logger.debug("No significant volume pattern detected")
    return None

#=== ANTI-MATRIX SIGNAL GENERATION ===
def generate_anti_matrix_signal(df):
    """Generate signals based on SMC and order flow"""
    if len(df) < 20:  # Increased from 10 for robustness
        logger.debug("Signal generation - Insufficient data")
        return None, 0, []
    
    current = df.iloc[-1]
    signals = []
    confidence = 0
    
    logger.debug("=== ANTI-MATRIX SIGNAL ANALYSIS ===")
    
    # 1. Check for trap candles
    trap = detect_trap_candles(df)
    if trap:
        if trap['type'] == 'bearish_trap':
            signals.append(f"Bearish trap at {trap['price']:.5f}")
            confidence += 2
            logger.debug(f"Signal Component - Bearish trap: +2 confidence")
        else:
            signals.append(f"Bullish trap at {trap['price']:.5f}")
            confidence += 2
            logger.debug(f"Signal Component - Bullish trap: +2 confidence")
    
    # 2. Check for structure breaks
    structure = detect_structure_breaks(df)
    if structure:
        signals.append(f"{structure['type']} {structure['direction']} at {structure['price']:.5f}")
        confidence += 2
        logger.debug(f"Signal Component - {structure['type']} {structure['direction']}: +2 confidence")
    
    # 3. Check for order block hits
    for ob in order_blocks:
        if ob['active']:
            if ob['type'] == 'bullish' and current['low'] <= ob['high']:
                signals.append(f"Bullish order block hit")
                confidence += 3
                logger.debug(f"Signal Component - Bullish order block hit: +3 confidence")
            elif ob['type'] == 'bearish' and current['high'] >= ob['low']:
                signals.append(f"Bearish order block hit")
                confidence += 3
                logger.debug(f"Signal Component - Bearish order block hit: +3 confidence")
    
    # 4. Check for FVG fills
    for fvg in fair_value_gaps:
        if not fvg['filled']:
            if fvg['type'] == 'bullish' and current['low'] <= fvg['start']:
                signals.append("Bullish FVG filled")
                confidence += 2
                logger.debug(f"Signal Component - Bullish FVG filled: +2 confidence")
            elif fvg['type'] == 'bearish' and current['high'] >= fvg['end']:
                signals.append("Bearish FVG filled")
                confidence += 2
                logger.debug(f"Signal Component - Bearish FVG filled: +2 confidence")
    
    # 5. Volume context (ENHANCED)
    volume_ctx = analyze_volume_context(df)
    if volume_ctx:
        # Only add volume signals if we have other confirmations
        if confidence >= 2:  # Only add volume context if we have other signals
            signals.append(f"Volume: {volume_ctx['type']}")
            confidence += 1
            logger.debug(f"Signal Component - Volume {volume_ctx['type']}: +1 confidence (with confirmation)")
        else:
            logger.debug(f"Volume signal ignored - insufficient other confirmations")
    
    # 6. AGGRESSIVE: Simple momentum signals (ENHANCED)
    if len(df) >= 3:
        prev1 = df.iloc[-2]
        prev2 = df.iloc[-3]
        
        # Strong momentum up with volume confirmation
        if (current['close'] > current['open'] and 
            prev1['close'] > prev1['open'] and 
            current['close'] > prev1['close'] and
            current['volume'] > df['volume'].rolling(window=10).mean().iloc[-1]):
            signals.append("Strong momentum up")
            confidence += 1
            logger.debug(f"Signal Component - Strong momentum up: +1 confidence")
        
        # Strong momentum down with volume confirmation
        elif (current['close'] < current['open'] and 
              prev1['close'] < prev1['open'] and 
              current['close'] < prev1['close'] and
              current['volume'] > df['volume'].rolling(window=10).mean().iloc[-1]):
            signals.append("Strong momentum down")
            confidence += 1
            logger.debug(f"Signal Component - Strong momentum down: +1 confidence")
    
    # 6.5. ADDITIONAL: Price action signals (ENHANCED)
    if len(df) >= 2:
        prev = df.iloc[-2]
        
        # Strong bearish candle with context
        if (current['close'] < current['open'] and 
            current['body'] > current['range'] * 0.6 and  # Strong body
            current['close'] < prev['close'] and  # Lower than previous
            current['volume'] > df['volume'].rolling(window=10).mean().iloc[-1]):  # Volume confirmation
            signals.append("Strong bearish candle")
            confidence += 1
            logger.debug(f"Signal Component - Strong bearish candle: +1 confidence")
        
        # Strong bullish candle with context
        elif (current['close'] > current['open'] and 
              current['body'] > current['range'] * 0.6 and  # Strong body
              current['close'] > prev['close'] and  # Higher than previous
              current['volume'] > df['volume'].rolling(window=10).mean().iloc[-1]):  # Volume confirmation
            signals.append("Strong bullish candle")
            confidence += 1
            logger.debug(f"Signal Component - Strong bullish candle: +1 confidence")
    
    # 7. AGGRESSIVE: Breakout signals (ENHANCED)
    if len(df) >= 10:
        recent_high = df['high'].rolling(window=10).max().iloc[-2]
        recent_low = df['low'].rolling(window=10).min().iloc[-2]
        
        # Breakout with volume confirmation
        if current['close'] > recent_high and current['volume'] > df['volume'].rolling(window=10).mean().iloc[-1]:
            signals.append("Breakout above resistance")
            confidence += 1
            logger.debug(f"Signal Component - Breakout above resistance: +1 confidence")
        elif current['close'] < recent_low and current['volume'] > df['volume'].rolling(window=10).mean().iloc[-1]:
            signals.append("Breakout below support")
            confidence += 1
            logger.debug(f"Signal Component - Breakout below support: +1 confidence")
    
    # 8. REAL-TIME ORDER BOOK ANALYSIS INTEGRATION
    try:
        # Use real-time order book analysis
        rt_bias, rt_reasoning, rt_confidence = analyze_real_time_order_book()
        
        # Also get cached order book for enhanced analysis
        order_book = ob_analyzer.fetch_order_book_cached(client)
        if order_book:
            # Use the enhanced analysis from orderbook_analysis_enhanced.py
            ob_bias, ob_reasoning, ob_confidence = ob_analyzer.analyze_order_book_anti_matrix_enhanced(
                order_book, current_price=current['close']
            )
            
            # Combine real-time and enhanced analysis
            if rt_bias != "neutral":
                signals.append(f"Real-time OB: {rt_bias} bias")
                confidence += rt_confidence
                logger.debug(f"Signal Component - Real-time Order Book {rt_bias} bias: +{rt_confidence} confidence")
                
                # Add real-time reasoning
                for reason in rt_reasoning:
                    signals.append(f"RT-OB: {reason}")
            
            if ob_bias != "neutral":
                signals.append(f"Enhanced OB: {ob_bias} bias")
                
                # Handle confidence value (might be list or int)
                if isinstance(ob_confidence, list):
                    ob_confidence_value = len(ob_confidence)  # Use length if it's a list
                else:
                    ob_confidence_value = int(ob_confidence)  # Convert to int
                
                confidence += ob_confidence_value
                logger.debug(f"Signal Component - Enhanced Order Book {ob_bias} bias: +{ob_confidence_value} confidence")
                
                # Add detailed reasoning from order book analysis
                if isinstance(ob_reasoning, list):
                    for reason in ob_reasoning:
                        if isinstance(reason, str):
                            signals.append(f"OB: {reason}")
                        else:
                            signals.append(f"OB: {str(reason)}")
                else:
                    # Handle case where ob_reasoning is not a list
                    signals.append(f"OB: {str(ob_reasoning)}")
            
            logger.info(f"Real-time Order Book - Bias: {rt_bias}, Confidence: {rt_confidence}")
            logger.info(f"Enhanced Order Book - Bias: {ob_bias}, Confidence: {ob_confidence}")
            logger.info(f"Market Regime: {ob_analyzer.market_regime}")
            
            # Get dynamic thresholds info
            thresholds = ob_analyzer.dynamic_thresholds.get_all_thresholds()
            logger.debug(f"Dynamic Thresholds: {thresholds}")
        else:
            logger.debug("Order book data not available")
    except Exception as e:
        logger.error(f"Error in order book analysis: {e}")
    
    # Determine signal direction (ENHANCED LOGIC)
    logger.debug(f"Signal Summary - Total confidence: {confidence}, Signals: {signals}")
    
    if confidence >= 3:
        # ENHANCED: More specific keyword matching
        bearish_keywords = ['bearish_trap', 'down', 'breakout below', 'momentum down', 'bearish order block hit', 'bearish fvg filled']
        bullish_keywords = ['bullish_trap', 'up', 'breakout above', 'momentum up', 'bullish order block hit', 'bullish fvg filled']
        
        # Volume patterns need context - don't use them alone for direction
        volume_signals = [s for s in signals if 'volume:' in s.lower()]
        other_signals = [s for s in signals if 'volume:' not in s.lower()]
        
        # VOLUME SIGNAL INTEGRATION: Use volume signals for confirmation
        volume_confirmation = 0
        volume_bias = 'neutral'
        
        for volume_signal in volume_signals:
            volume_signal_lower = volume_signal.lower()
            # Bullish volume signals
            if any(bullish_type in volume_signal_lower for bullish_type in ['accumulation', 'support_bounce', 'high_volume_bullish', 'moderate_bullish']):
                volume_bias = 'bullish'
                volume_confirmation += 1
                logger.debug(f"Volume confirmation - Bullish: {volume_signal}")
            # Bearish volume signals
            elif any(bearish_type in volume_signal_lower for bearish_type in ['distribution', 'resistance_rejection', 'high_volume_bearish', 'moderate_bearish']):
                volume_bias = 'bearish'
                volume_confirmation += 1
                logger.debug(f"Volume confirmation - Bearish: {volume_signal}")
            # Neutral volume signals
            elif any(neutral_type in volume_signal_lower for neutral_type in ['consolidation', 'low_activity']):
                volume_confirmation += 1
                logger.debug(f"Volume confirmation - Neutral: {volume_signal}")
        
        # ENHANCED: More precise signal classification
        bearish_signals = []
        bullish_signals = []
        
        for signal in other_signals:
            signal_lower = signal.lower()
            # Check for exact matches first
            if any(keyword in signal_lower for keyword in bearish_keywords):
                bearish_signals.append(signal)
            elif any(keyword in signal_lower for keyword in bullish_keywords):
                bullish_signals.append(signal)
            # Handle structure breaks
            elif 'bos' in signal_lower and 'up' in signal_lower:
                bullish_signals.append(signal)
            elif 'bos' in signal_lower and 'down' in signal_lower:
                bearish_signals.append(signal)
            elif 'choch' in signal_lower and 'up' in signal_lower:
                bullish_signals.append(signal)
            elif 'choch' in signal_lower and 'down' in signal_lower:
                bearish_signals.append(signal)
            # Handle order book bias
            elif 'real-time ob: bearish' in signal_lower or 'enhanced ob: bearish' in signal_lower:
                bearish_signals.append(signal)
            elif 'real-time ob: bullish' in signal_lower or 'enhanced ob: bullish' in signal_lower:
                bullish_signals.append(signal)
        
        # VOLUME CONFIRMATION INTEGRATION: Use volume bias to confirm signals
        final_bearish_count = len(bearish_signals)
        final_bullish_count = len(bullish_signals)
        
        # Apply volume confirmation bias
        if volume_bias == 'bullish' and volume_confirmation > 0:
            final_bullish_count += volume_confirmation
            logger.debug(f"Volume confirmation applied: +{volume_confirmation} to bullish count")
        elif volume_bias == 'bearish' and volume_confirmation > 0:
            final_bearish_count += volume_confirmation
            logger.debug(f"Volume confirmation applied: +{volume_confirmation} to bearish count")
        
        # ENHANCED: Require multiple confirmations for high-confidence trades
        if final_bearish_count >= 2 and final_bearish_count > final_bullish_count:
            logger.debug(f"Signal Decision - SHORT (strong bearish signals: {bearish_signals}, volume confirmation: {volume_confirmation})")
            return "SHORT", confidence, signals
        elif final_bullish_count >= 2 and final_bullish_count > final_bearish_count:
            logger.debug(f"Signal Decision - LONG (strong bullish signals: {bullish_signals}, volume confirmation: {volume_confirmation})")
            return "LONG", confidence, signals
        elif final_bearish_count == 1 and final_bullish_count == 0 and confidence >= 4:
            logger.debug(f"Signal Decision - SHORT (single strong bearish signal: {bearish_signals}, volume confirmation: {volume_confirmation})")
            return "SHORT", confidence, signals
        elif final_bullish_count == 1 and final_bearish_count == 0 and confidence >= 4:
            logger.debug(f"Signal Decision - LONG (single strong bullish signal: {bullish_signals}, volume confirmation: {volume_confirmation})")
            return "LONG", confidence, signals
        else:
            logger.debug(f"Signal Decision - WAIT (insufficient directional confirmation: {signals}, volume confirmation: {volume_confirmation})")
            return "WAIT", confidence, signals
    
    logger.debug("Signal Decision - WAIT (insufficient confidence or mixed signals)")
    return "WAIT", confidence, signals

#=== ADAPTIVE SL/TP CALCULATION ===
def calculate_adaptive_levels(signal, entry_price, df):
    """Calculate SL/TP based on F&O dynamics and market conditions"""
    if len(df) < 10:  # Increased from 5 for robustness
        logger.debug("Adaptive levels - Using default F&O levels")
        return entry_price * 0.95, entry_price * 1.10  # Default 5% SL, 10% TP for F&O
    
    # Calculate market volatility for dynamic SL/TP
    recent_prices = df['close'].tail(20)
    volatility = recent_prices.std() / recent_prices.mean()
    
    # Calculate average true range (ATR) for dynamic levels
    df['tr'] = np.maximum(
        df['high'] - df['low'],
        np.maximum(
            abs(df['high'] - df['close'].shift(1)),
            abs(df['low'] - df['close'].shift(1))
        )
    )
    atr = df['tr'].tail(14).mean()
    
    # Find nearest liquidity levels
    nearest_high = min([level['price'] for level in liquidity_levels['highs']], default=entry_price * 1.05)
    nearest_low = max([level['price'] for level in liquidity_levels['lows']], default=entry_price * 0.95)
    
    logger.debug(f"F&O Adaptive Levels - Entry: {entry_price:.5f}, Volatility: {volatility:.4f}, ATR: {atr:.5f}")
    logger.debug(f"Liquidity Levels - High: {nearest_high:.5f}, Low: {nearest_low:.5f}")
    
    if signal == "LONG":
        # Dynamic SL based on volatility and liquidity
        sl_distance = max(atr * 1.5, entry_price * 0.03)  # At least 3% or 1.5x ATR
        stop_loss = max(nearest_low, entry_price - sl_distance)
        
        # Dynamic TP based on market potential
        # Check if there's strong bullish momentum
        recent_momentum = (df['close'].iloc[-1] - df['close'].iloc[-5]) / df['close'].iloc[-5]
        
        if recent_momentum > 0.02:  # Strong bullish momentum (>2%)
            # Higher TP for strong momentum
            tp_distance = max(entry_price * 0.15, atr * 3)  # 15% or 3x ATR
            logger.debug(f"Strong bullish momentum detected: {recent_momentum:.2%} - Using higher TP")
        elif recent_momentum > 0.01:  # Moderate bullish momentum (>1%)
            # Medium TP
            tp_distance = max(entry_price * 0.12, atr * 2.5)  # 12% or 2.5x ATR
            logger.debug(f"Moderate bullish momentum detected: {recent_momentum:.2%} - Using medium TP")
        else:
            # Standard TP
            tp_distance = max(entry_price * 0.10, atr * 2)  # 10% or 2x ATR
            logger.debug(f"Standard momentum: {recent_momentum:.2%} - Using standard TP")
                
        take_profit = entry_price + tp_distance
        
        # Ensure minimum 1:2 risk-reward ratio
        min_tp = entry_price + (entry_price - stop_loss) * 2
        if take_profit < min_tp:
            take_profit = min_tp
            logger.debug(f"Adjusted TP to maintain minimum 1:2 RR")
        
        logger.debug(f"LONG F&O Levels - SL: {stop_loss:.5f} ({((stop_loss/entry_price)-1)*100:.1f}%), TP: {take_profit:.5f} ({((take_profit/entry_price)-1)*100:.1f}%)")
        
    else:  # SHORT
        # Dynamic SL based on volatility and liquidity
        sl_distance = max(atr * 1.5, entry_price * 0.03)  # At least 3% or 1.5x ATR
        stop_loss = min(nearest_high, entry_price + sl_distance)
        
        # Dynamic TP based on market potential
        # Check if there's strong bearish momentum
        recent_momentum = (df['close'].iloc[-5] - df['close'].iloc[-1]) / df['close'].iloc[-5]
        
        if recent_momentum > 0.02:  # Strong bearish momentum (>2%)
            # Higher TP for strong momentum
            tp_distance = max(entry_price * 0.15, atr * 3)  # 15% or 3x ATR
            logger.debug(f"Strong bearish momentum detected: {recent_momentum:.2%} - Using higher TP")
        elif recent_momentum > 0.01:  # Moderate bearish momentum (>1%)
            # Medium TP
            tp_distance = max(entry_price * 0.12, atr * 2.5)  # 12% or 2.5x ATR
            logger.debug(f"Moderate bearish momentum detected: {recent_momentum:.2%} - Using medium TP")
        else:
            # Standard TP
            tp_distance = max(entry_price * 0.10, atr * 2)  # 10% or 2x ATR
            logger.debug(f"Standard momentum: {recent_momentum:.2%} - Using standard TP")
        
        take_profit = entry_price - tp_distance
        
        # Ensure minimum 1:2 risk-reward ratio
        min_tp = entry_price - (stop_loss - entry_price) * 2
        if take_profit > min_tp:
            take_profit = min_tp
            logger.debug(f"Adjusted TP to maintain minimum 1:2 RR")
        
        logger.debug(f"SHORT F&O Levels - SL: {stop_loss:.5f} ({((stop_loss/entry_price)-1)*100:.1f}%), TP: {take_profit:.5f} ({((take_profit/entry_price)-1)*100:.1f}%)")
    
    return stop_loss, take_profit

#=== EXECUTION WITH SLIPPAGE ===
def execute_with_slippage(signal, entry_price, position_size):
    """Simulate realistic execution with slippage"""
    # Simulate 0.1% slippage
    slippage = entry_price * 0.001
    
    if signal == "LONG":
        fill_price = entry_price + slippage  # Buy at ask
        logger.debug(f"Execution - LONG at ask: {fill_price:.5f} (slippage: +{slippage:.5f})")
    else:
        fill_price = entry_price - slippage  # Sell at bid
        logger.debug(f"Execution - SHORT at bid: {fill_price:.5f} (slippage: -{slippage:.5f})")
    
    return fill_price

#=== REAL-TIME ORDER BOOK FUNCTIONS ===
def start_real_time_order_book_streaming():
    """Start real-time order book streaming in parallel"""
    global order_book_streaming, order_book_ws, order_book_thread
    
    if order_book_streaming:
        return
    
    try:
        order_book_streaming = True
        
        # Create WebSocket connection for order book
        order_book_ws = websocket.WebSocketApp(
            f"wss://fstream.binance.com/ws/{symbol}@depth20@100ms",
            on_message=on_order_book_message,
            on_error=on_order_book_error,
            on_close=on_order_book_close,
            on_open=on_order_book_open
        )
        
        # Start streaming in separate thread
        order_book_thread = threading.Thread(target=order_book_ws.run_forever)
        order_book_thread.daemon = True
        order_book_thread.start()
        
        logger.info(f"🔄 Real-time order book streaming started for {symbol}")
        
    except Exception as e:
        logger.error(f"❌ Failed to start order book streaming: {e}")
        order_book_streaming = False

def on_order_book_message(ws, message):
    """Handle real-time order book updates"""
    try:
        data = json.loads(message)
        
        # Extract order book data
        bids = [(float(price), float(qty)) for price, qty in data.get('bids', [])]
        asks = [(float(price), float(qty)) for price, qty in data.get('asks', [])]
        
        if bids and asks:
            # Calculate order flow delta
            delta = calculate_order_flow_delta(bids, asks)
            
            # Store order book data
            order_book_data = {
                'timestamp': time.time(),
                'bids': bids,
                'asks': asks,
                'delta': delta,
                'mid_price': (bids[0][0] + asks[0][0]) / 2
            }
            
            # Add to buffer (keep last 100 entries)
            order_book_buffer.append(order_book_data)
            if len(order_book_buffer) > 100:
                order_book_buffer.pop(0)
            
            # Update order flow history
            order_flow_history.append({
                'timestamp': time.time(),
                'delta': delta,
                'mid_price': order_book_data['mid_price']
            })
            if len(order_flow_history) > 50:
                order_flow_history.pop(0)
            
            # Real-time pattern detection
            patterns = detect_real_time_patterns(order_book_data)
            if patterns:
                real_time_patterns.extend(patterns)
                # Keep only recent patterns
                if len(real_time_patterns) > 20:
                    real_time_patterns.pop(0)
            
            logger.debug(f"📊 Real-time order book: Delta={delta:.4f}, Patterns={len(patterns)}")
            
    except Exception as e:
        logger.error(f"❌ Error processing order book message: {e}")

def on_order_book_error(ws, error):
    """Handle order book WebSocket errors"""
    logger.error(f"❌ Order book WebSocket error: {error}")

def on_order_book_close(ws, close_status_code, close_msg):
    """Handle order book WebSocket close"""
    global order_book_streaming
    order_book_streaming = False
    logger.info(f"🔌 Order book WebSocket closed: {close_status_code} - {close_msg}")

def on_order_book_open(ws):
    """Handle order book WebSocket open"""
    logger.info("🔌 Order book WebSocket opened")

def calculate_order_flow_delta(bids, asks):
    """Calculate order flow delta"""
    total_bid_volume = sum(qty for _, qty in bids[:10])
    total_ask_volume = sum(qty for _, qty in asks[:10])
    
    if total_bid_volume + total_ask_volume == 0:
        return 0
    
    delta = (total_bid_volume - total_ask_volume) / (total_bid_volume + total_ask_volume)
    return delta

def detect_real_time_patterns(order_book_data):
    """Detect real-time patterns in order book"""
    patterns = []
    
    try:
        bids = order_book_data['bids']
        asks = order_book_data['asks']
        delta = order_book_data['delta']
        
        # Detect large buy walls
        large_bid_walls = [qty for _, qty in bids[:5] if qty > 100000]
        if len(large_bid_walls) >= 3:
            patterns.append("Large buy walls detected")
        
        # Detect large sell walls
        large_ask_walls = [qty for _, qty in asks[:5] if qty > 100000]
        if len(large_ask_walls) >= 3:
            patterns.append("Large sell walls detected")
        
        # Detect order flow imbalance
        if abs(delta) > 0.3:
            if delta > 0:
                patterns.append("Strong bid dominance")
            else:
                patterns.append("Strong ask dominance")
        
        # Detect layering
        bid_layers = [bids[i][1] for i in range(min(5, len(bids)))]
        ask_layers = [asks[i][1] for i in range(min(5, len(asks)))]
        
        if len(bid_layers) >= 3 and all(bid_layers[i] >= bid_layers[i+1] for i in range(len(bid_layers)-1)):
            patterns.append("Bid layering detected")
        
        if len(ask_layers) >= 3 and all(ask_layers[i] >= ask_layers[i+1] for i in range(len(ask_layers)-1)):
            patterns.append("Ask layering detected")
        
    except Exception as e:
        logger.error(f"❌ Error detecting real-time patterns: {e}")
    
    return patterns

def get_order_book_data_for_period(start_time, end_time):
    """Get order book data for a specific time period"""
    period_data = []
    
    for data in order_book_buffer:
        if start_time <= data['timestamp'] <= end_time:
            period_data.append(data)
    
    return period_data

def analyze_real_time_order_book():
    """Analyze real-time order book data"""
    if not order_book_buffer:
        return "neutral", [], 0
    
    try:
        # Get recent order book data
        recent_data = order_book_buffer[-10:]  # Last 10 updates
        
        # Calculate average delta
        avg_delta = sum(d['delta'] for d in recent_data) / len(recent_data)
        
        # Count patterns
        bullish_patterns = sum(1 for p in real_time_patterns if "buy" in p.lower() or "bid" in p.lower())
        bearish_patterns = sum(1 for p in real_time_patterns if "sell" in p.lower() or "ask" in p.lower())
        
        # Determine bias
        if avg_delta > 0.1 and bullish_patterns > bearish_patterns:
            bias = "bullish"
            confidence = min(3, bullish_patterns + 1)
            reasoning = [p for p in real_time_patterns if "buy" in p.lower() or "bid" in p.lower()]
        elif avg_delta < -0.1 and bearish_patterns > bullish_patterns:
            bias = "bearish"
            confidence = min(3, bearish_patterns + 1)
            reasoning = [p for p in real_time_patterns if "sell" in p.lower() or "ask" in p.lower()]
        else:
            bias = "neutral"
            confidence = 0
            reasoning = []
        
        return bias, reasoning, confidence
        
    except Exception as e:
        logger.error(f"❌ Error analyzing real-time order book: {e}")
        return "neutral", [], 0

# Add global variable for tracking multi-candle reasons
multi_candle_reasons = []  # Track reasons from last 10 candles

# def analyze_bigger_picture(signal, confidence, reasons):
#     """Analyze the bigger picture from ANTI-MATRIX perspective - what institutions are really doing vs what retail sees"""
#     global multi_candle_reasons
    
#     # Add current reasons to multi-candle tracking (keep last 10 candles)
#     multi_candle_reasons.append({
#         'timestamp': time.time(),
#         'signal': signal,
#         'confidence': confidence,
#         'reasons': reasons
#     })
    
#     # Keep only last 10 candles
#     if len(multi_candle_reasons) > 10:
#         multi_candle_reasons = multi_candle_reasons[-10:]
    
#     # Analyze patterns across multiple candles
#     all_reasons = []
#     signal_trend = []
#     confidence_trend = []
    
#     for candle_data in multi_candle_reasons:
#         all_reasons.extend(candle_data['reasons'])
#         signal_trend.append(candle_data['signal'])
#         confidence_trend.append(candle_data['confidence'])
    
#     # ANTI-MATRIX PATTERN ANALYSIS
#     institutional_traps = []
#     retail_manipulation = []
#     contrarian_signals = []
#     liquidity_grabs = []
#     stop_hunting = []
    
#     for reason in all_reasons:
#         reason_lower = reason.lower()
        
#         # Institutional Traps (what looks bullish but is bearish)
#         if any(keyword in reason_lower for keyword in ['ask layering', 'bullish trap', 'accumulation']):
#             institutional_traps.append(reason)
        
#         # Retail Manipulation (what institutions do to fool retail)
#         elif any(keyword in reason_lower for keyword in ['session manipulation', 'liquidity grab', 'stop hunting']):
#             retail_manipulation.append(reason)
        
#         # Contrarian Signals (going against obvious)
#         elif any(keyword in reason_lower for keyword in ['bearish bias', 'distribution']):
#             contrarian_signals.append(reason)
        
#         # Liquidity Grabs (institutions taking retail money)
#         elif 'liquidity grab' in reason_lower:
#             liquidity_grabs.append(reason)
        
#         # Stop Hunting (institutions hunting retail stops)
#         elif 'stop hunting' in reason_lower:
#             stop_hunting.append(reason)
    
#     # ANTI-MATRIX MARKET CONTEXT
#     institutional_agenda = "unknown"
#     retail_trap = "unknown"
#     anti_matrix_logic = "unknown"
    
#     # Determine institutional agenda
#     if institutional_traps:
#         if len(institutional_traps) > 2:
#             institutional_agenda = "heavy institutional trap setting - they want retail to buy so they can dump"
#         else:
#             institutional_agenda = "institutional trap detected - they're setting up retail for a dump"
    
#     if retail_manipulation:
#         if len(retail_manipulation) > 2:
#             retail_trap = "heavy retail manipulation - institutions are actively fooling retail traders"
#         else:
#             retail_trap = "retail manipulation detected - institutions are creating false signals"
    
#     # Determine anti-matrix logic
#     if signal == "SHORT":
#         if institutional_traps:
#             anti_matrix_logic = "going SHORT because institutions are setting bullish traps to dump on retail"
#         elif contrarian_signals:
#             anti_matrix_logic = "going SHORT against obvious bullish signals - anti-matrix contrarian logic"
#         else:
#             anti_matrix_logic = "going SHORT based on institutional bearish bias"
    
#     elif signal == "LONG":
#         if institutional_traps:
#             anti_matrix_logic = "going LONG because institutions are setting bearish traps to pump and dump"
#         elif contrarian_signals:
#             anti_matrix_logic = "going LONG against obvious bearish signals - anti-matrix contrarian logic"
#         else:
#             anti_matrix_logic = "going LONG based on institutional bullish bias"
    
#     else:  # WAIT
#         anti_matrix_logic = "waiting because institutional agenda is unclear - avoiding retail traps"
    
#     # ANTI-MATRIX FUTURE EXPECTATIONS
#     institutional_plan = ""
#     retail_expectation = ""
#     anti_matrix_prediction = ""
    
#     if signal == "SHORT":
#         if institutional_traps:
#             institutional_plan = "institutions will pump price to trap retail buyers, then dump"
#             retail_expectation = "retail will see bullish signals and buy"
#             anti_matrix_prediction = "we go SHORT to catch the dump after the pump"
#         else:
#             institutional_plan = "institutions are bearish and will push price down"
#             retail_expectation = "retail may still be bullish"
#             anti_matrix_prediction = "we go SHORT with institutions against retail"
    
#     elif signal == "LONG":
#         if institutional_traps:
#             institutional_plan = "institutions will dump price to trap retail sellers, then pump"
#             retail_expectation = "retail will see bearish signals and sell"
#             anti_matrix_prediction = "we go LONG to catch the pump after the dump"
#         else:
#             institutional_plan = "institutions are bullish and will push price up"
#             retail_expectation = "retail may still be bearish"
#             anti_matrix_prediction = "we go LONG with institutions against retail"
    
#     else:  # WAIT
#         institutional_plan = "institutions are unclear, avoiding potential traps"
#         retail_expectation = "retail may be confused"
#         anti_matrix_prediction = "we wait for clearer institutional direction"
    
#     # ANTI-MATRIX ACTION RECOMMENDATIONS
#     what_to_do = ""
#     what_not_to_do = ""
    
#     if signal == "SHORT":
#         if institutional_traps:
#             what_to_do = "✅ GO SHORT - Institutions setting bullish traps, expect pump then dump"
#             what_not_to_do = "❌ DON'T BUY - That's exactly what institutions want you to do"
#         else:
#             what_to_do = "✅ GO SHORT - Following institutional bearish bias"
#             what_not_to_do = "❌ DON'T FIGHT INSTITUTIONS - They control the market"
    
#     elif signal == "LONG":
#         if institutional_traps:
#             what_to_do = "✅ GO LONG - Institutions setting bearish traps, expect dump then pump"
#             what_not_to_do = "❌ DON'T SELL - That's exactly what institutions want you to do"
#         else:
#             what_to_do = "✅ GO LONG - Following institutional bullish bias"
#             what_not_to_do = "❌ DON'T FIGHT INSTITUTIONS - They control the market"
    
#     else:  # WAIT
#         what_to_do = "✅ STAY OUT - Institutional agenda unclear, avoid traps"
#         what_not_to_do = "❌ DON'T FORCE TRADES - Wait for clear institutional direction"
    
#     # Generate anti-matrix narrative
#     anti_matrix_narrative = f"ANTI-MATRIX ANALYSIS: "
#     anti_matrix_narrative += f"Institutional Agenda: {institutional_agenda}. "
#     anti_matrix_narrative += f"Retail Trap: {retail_trap}. "
#     anti_matrix_narrative += f"Anti-Matrix Logic: {anti_matrix_logic}. "
#     anti_matrix_narrative += f"Institutional Plan: {institutional_plan}. "
#     anti_matrix_narrative += f"Retail Expectation: {retail_expectation}. "
#     anti_matrix_narrative += f"Anti-Matrix Prediction: {anti_matrix_prediction}."
    
#     # Add confidence context
#     if confidence >= 10:
#         confidence_context = "Very strong anti-matrix conviction"
#     elif confidence >= 7:
#         confidence_context = "Strong anti-matrix conviction"
#     elif confidence >= 5:
#         confidence_context = "Moderate anti-matrix conviction"
#     else:
#         confidence_context = "Weak anti-matrix conviction"
    
#     logger.info(f"�� ANTI-MATRIX BIGGER PICTURE:")
#     logger.info(f"   Signal: {signal} | Confidence: {confidence} ({confidence_context})")
#     logger.info(f"   Institutional Agenda: {institutional_agenda}")
#     logger.info(f"   Retail Trap: {retail_trap}")
#     logger.info(f"   Anti-Matrix Logic: {anti_matrix_logic}")
#     logger.info(f"   Institutional Plan: {institutional_plan}")
#     logger.info(f"   Retail Expectation: {retail_expectation}")
#     logger.info(f"   Anti-Matrix Prediction: {anti_matrix_prediction}")
#     logger.info(f"   WHAT TO DO: {what_to_do}")
#     logger.info(f"   WHAT NOT TO DO: {what_not_to_do}")
#     logger.info(f"   Narrative: {anti_matrix_narrative}")
    
#     return {
#         'signal': signal,
#         'confidence': confidence,
#         'institutional_agenda': institutional_agenda,
#         'retail_trap': retail_trap,
#         'anti_matrix_logic': anti_matrix_logic,
#         'institutional_plan': institutional_plan,
#         'retail_expectation': retail_expectation,
#         'anti_matrix_prediction': anti_matrix_prediction,
#         'what_to_do': what_to_do,
#         'what_not_to_do': what_not_to_do,
#         'narrative': anti_matrix_narrative,
#         'confidence_context': confidence_context,
#         'candles_analyzed': len(multi_candle_reasons)
#     }

# def analyze_bigger_picture(signal, confidence, reasons):
#     """Comprehensive Anti-Matrix Analysis - Understanding the game and avoiding traps"""
#     global multi_candle_reasons
    
#     # Add current reasons to multi-candle tracking
#     multi_candle_reasons.append({
#         'timestamp': time.time(),
#         'signal': signal,
#         'confidence': confidence,
#         'reasons': reasons
#     })
    
#     # Keep only last 10 candles
#     if len(multi_candle_reasons) > 10:
#         multi_candle_reasons = multi_candle_reasons[-10:]
    
#     # ANTI-MATRIX GAME ANALYSIS
#     institutional_behavior = {}
#     retail_psychology = {}
#     trap_identification = {}
#     market_dynamics = {}
    
#     # Analyze patterns across multiple candles
#     all_reasons = []
#     signal_trend = []
#     confidence_trend = []
    
#     for candle_data in multi_candle_reasons:
#         all_reasons.extend(candle_data['reasons'])
#         signal_trend.append(candle_data['signal'])
#         confidence_trend.append(candle_data['confidence'])
    
#     # COMPREHENSIVE PATTERN ANALYSIS
#     for reason in all_reasons:
#         reason_lower = reason.lower()
        
#         # Institutional Behavior Patterns
#         if 'ask layering' in reason_lower:
#             institutional_behavior['ask_layering'] = institutional_behavior.get('ask_layering', 0) + 1
#             trap_identification['bullish_trap'] = trap_identification.get('bullish_trap', 0) + 1
#         elif 'bid layering' in reason_lower:
#             institutional_behavior['bid_layering'] = institutional_behavior.get('bid_layering', 0) + 1
#             trap_identification['bearish_trap'] = trap_identification.get('bearish_trap', 0) + 1
#         elif 'liquidity grab' in reason_lower:
#             institutional_behavior['liquidity_grab'] = institutional_behavior.get('liquidity_grab', 0) + 1
#             trap_identification['stop_hunting'] = trap_identification.get('stop_hunting', 0) + 1
#         elif 'stop hunting' in reason_lower:
#             institutional_behavior['stop_hunting'] = institutional_behavior.get('stop_hunting', 0) + 1
#             trap_identification['stop_hunting'] = trap_identification.get('stop_hunting', 0) + 1
        
#         # Market Phase Patterns
#         elif 'accumulation' in reason_lower:
#             institutional_behavior['accumulation'] = institutional_behavior.get('accumulation', 0) + 1
#             market_dynamics['institutional_buying'] = market_dynamics.get('institutional_buying', 0) + 1
#         elif 'distribution' in reason_lower:
#             institutional_behavior['distribution'] = institutional_behavior.get('distribution', 0) + 1
#             market_dynamics['institutional_selling'] = market_dynamics.get('institutional_selling', 0) + 1
        
#         # Retail Psychology Patterns
#         elif 'session manipulation' in reason_lower:
#             retail_psychology['manipulation'] = retail_psychology.get('manipulation', 0) + 1
#         elif 'volume' in reason_lower:
#             retail_psychology['volume_reaction'] = retail_psychology.get('volume_reaction', 0) + 1
    
#     # ANTI-MATRIX GAME UNDERSTANDING
#     game_phase = "unknown"
#     institutional_agenda = "unknown"
#     retail_trap = "unknown"
#     anti_matrix_strategy = "unknown"
    
#     # Determine Game Phase
#     if institutional_behavior.get('ask_layering', 0) > 2:
#         game_phase = "institutional trap setting - they want retail to buy so they can dump"
#     elif institutional_behavior.get('bid_layering', 0) > 2:
#         game_phase = "institutional trap setting - they want retail to sell so they can pump"
#     elif institutional_behavior.get('liquidity_grab', 0) > 2:
#         game_phase = "institutional stop hunting - they're hunting retail stops"
#     elif institutional_behavior.get('accumulation', 0) > 2:
#         game_phase = "institutional accumulation - they're buying while retail is selling"
#     elif institutional_behavior.get('distribution', 0) > 2:
#         game_phase = "institutional distribution - they're selling while retail is buying"
#     else:
#         game_phase = "neutral phase - unclear institutional agenda"
    
#     # Determine Institutional Agenda
#     if 'ask_layering' in game_phase:
#         institutional_agenda = "institutions setting bullish traps to dump on retail"
#     elif 'bid_layering' in game_phase:
#         institutional_agenda = "institutions setting bearish traps to pump and dump"
#     elif 'stop hunting' in game_phase:
#         institutional_agenda = "institutions hunting retail stops for liquidity"
#     elif 'accumulation' in game_phase:
#         institutional_agenda = "institutions accumulating for a major move"
#     elif 'distribution' in game_phase:
#         institutional_agenda = "institutions distributing for a major move"
#     else:
#         institutional_agenda = "institutional agenda unclear"
    
#     # Determine Retail Trap
#     if institutional_behavior.get('ask_layering', 0) > 2:
#         retail_trap = "retail will see bullish signals and buy (FOMO trap)"
#     elif institutional_behavior.get('bid_layering', 0) > 2:
#         retail_trap = "retail will see bearish signals and sell (panic trap)"
#     elif institutional_behavior.get('liquidity_grab', 0) > 2:
#         retail_trap = "retail will panic sell at stops (liquidity trap)"
#     else:
#         retail_trap = "retail behavior unclear"
    
#     # ANTI-MATRIX STRATEGY
#     if signal == "SHORT":
#         if 'ask_layering' in game_phase:
#             anti_matrix_strategy = "GO SHORT - Avoid bullish trap, catch the dump after pump"
#         elif 'distribution' in game_phase:
#             anti_matrix_strategy = "GO SHORT - Follow institutional selling, avoid retail buying"
#         elif 'stop_hunting' in game_phase:
#             anti_matrix_strategy = "GO SHORT - Catch institutional stop hunt"
#         else:
#             anti_matrix_strategy = "GO SHORT - Anti-matrix bearish bias"
    
#     elif signal == "LONG":
#         if 'bid_layering' in game_phase:
#             anti_matrix_strategy = "GO LONG - Avoid bearish trap, catch the pump after dump"
#         elif 'accumulation' in game_phase:
#             anti_matrix_strategy = "GO LONG - Follow institutional buying, avoid retail selling"
#         elif 'stop_hunting' in game_phase:
#             anti_matrix_strategy = "GO LONG - Catch institutional stop hunt"
#         else:
#             anti_matrix_strategy = "GO LONG - Anti-matrix bullish bias"
    
#     else:  # WAIT
#         anti_matrix_strategy = "WAIT - Avoid unclear situations, preserve capital"
    
#     # ANTI-MATRIX PREDICTIONS
#     institutional_plan = ""
#     retail_expectation = ""
#     anti_matrix_prediction = ""
#     next_candles_expectation = ""
    
#     if signal == "SHORT":
#         if 'ask_layering' in game_phase:
#             institutional_plan = "institutions will pump price to trap retail buyers, then dump"
#             retail_expectation = "retail will FOMO buy during the pump"
#             anti_matrix_prediction = "we go SHORT to catch the dump after the pump"
#             next_candles_expectation = "expect pump in next 2-5 candles, dump in 5-10 candles"
#         elif 'distribution' in game_phase:
#             institutional_plan = "institutions will continue selling while retail buys"
#             retail_expectation = "retail will buy the dip and get trapped"
#             anti_matrix_prediction = "we go SHORT with institutions against retail"
#             next_candles_expectation = "expect continued downward pressure for 5-15 candles"
#         elif 'stop_hunting' in game_phase:
#             institutional_plan = "institutions will hunt retail stops downward"
#             retail_expectation = "retail will panic sell at stops"
#             anti_matrix_prediction = "we go SHORT to catch the stop hunt"
#             next_candles_expectation = "expect sharp downward move in next 1-3 candles"
#         else:
#             institutional_plan = "institutions are bearish and will push price down"
#             retail_expectation = "retail may still be bullish"
#             anti_matrix_prediction = "we go SHORT with institutions against retail"
#             next_candles_expectation = "expect gradual downward movement over 10-20 candles"
    
#     elif signal == "LONG":
#         if 'bid_layering' in game_phase:
#             institutional_plan = "institutions will dump price to trap retail sellers, then pump"
#             retail_expectation = "retail will panic sell during the dump"
#             anti_matrix_prediction = "we go LONG to catch the pump after the dump"
#             next_candles_expectation = "expect dump in next 2-5 candles, pump in 5-10 candles"
#         elif 'accumulation' in game_phase:
#             institutional_plan = "institutions will continue buying while retail sells"
#             retail_expectation = "retail will sell the rally and get trapped"
#             anti_matrix_prediction = "we go LONG with institutions against retail"
#             next_candles_expectation = "expect continued upward pressure for 5-15 candles"
#         elif 'stop_hunting' in game_phase:
#             institutional_plan = "institutions will hunt retail stops upward"
#             retail_expectation = "retail will panic buy at stops"
#             anti_matrix_prediction = "we go LONG to catch the stop hunt"
#             next_candles_expectation = "expect sharp upward move in next 1-3 candles"
#         else:
#             institutional_plan = "institutions are bullish and will push price up"
#             retail_expectation = "retail may still be bearish"
#             anti_matrix_prediction = "we go LONG with institutions against retail"
#             next_candles_expectation = "expect gradual upward movement over 10-20 candles"
    
#     else:  # WAIT
#         institutional_plan = "institutions are unclear, avoiding potential traps"
#         retail_expectation = "retail may be confused"
#         anti_matrix_prediction = "we wait for clearer institutional direction"
#         next_candles_expectation = "wait 5-10 candles for clearer signals"
    
#     # ANTI-MATRIX ACTION RECOMMENDATIONS
#     what_to_do = ""
#     what_not_to_do = ""
#     risk_management = ""
#     profit_strategy = ""
    
#     if signal == "SHORT":
#         if 'ask_layering' in game_phase:
#             what_to_do = "✅ GO SHORT - Avoid bullish trap, catch institutional dump"
#             what_not_to_do = "❌ DON'T FOMO BUY - That's exactly what institutions want"
#             risk_management = "��️ Use wider stops - expect pump before dump"
#             profit_strategy = "💰 Target dump after pump - be patient with trap"
#         elif 'distribution' in game_phase:
#             what_to_do = "✅ GO SHORT - Follow institutional selling"
#             what_not_to_do = "❌ DON'T BUY THE DIP - Institutions are selling"
#             risk_management = "🛡️ Use standard stops - steady downward pressure"
#             profit_strategy = "💰 Target 2-3x risk reward - distribution may continue"
#         elif 'stop_hunting' in game_phase:
#             what_to_do = "✅ GO SHORT - Catch institutional stop hunt"
#             what_not_to_do = "❌ DON'T USE TIGHT STOPS - You'll get hunted"
#             risk_management = "��️ Use wider stops - expect volatility"
#             profit_strategy = "💰 Quick profits - stop hunt may be brief"
#         else:
#             what_to_do = "✅ GO SHORT - Anti-matrix bearish bias"
#             what_not_to_do = "❌ DON'T FIGHT INSTITUTIONS - They control the market"
#             risk_management = "🛡️ Use standard stops"
#             profit_strategy = "💰 Target 2x risk reward"
    
#     elif signal == "LONG":
#         if 'bid_layering' in game_phase:
#             what_to_do = "✅ GO LONG - Avoid bearish trap, catch institutional pump"
#             what_not_to_do = "❌ DON'T PANIC SELL - That's exactly what institutions want"
#             risk_management = "��️ Use wider stops - expect dump before pump"
#             profit_strategy = "💰 Target pump after dump - be patient with trap"
#         elif 'accumulation' in game_phase:
#             what_to_do = "✅ GO LONG - Follow institutional buying"
#             what_not_to_do = "❌ DON'T SELL THE RALLY - Institutions are buying"
#             risk_management = "🛡️ Use standard stops - steady upward pressure"
#             profit_strategy = "💰 Target 2-3x risk reward - accumulation may continue"
#         elif 'stop_hunting' in game_phase:
#             what_to_do = "✅ GO LONG - Catch institutional stop hunt"
#             what_not_to_do = "❌ DON'T USE TIGHT STOPS - You'll get hunted"
#             risk_management = "��️ Use wider stops - expect volatility"
#             profit_strategy = "💰 Quick profits - stop hunt may be brief"
#         else:
#             what_to_do = "✅ GO LONG - Anti-matrix bullish bias"
#             what_not_to_do = "❌ DON'T FIGHT INSTITUTIONS - They control the market"
#             risk_management = "🛡️ Use standard stops"
#             profit_strategy = "💰 Target 2x risk reward"
    
#     else:  # WAIT
#         what_to_do = "✅ STAY OUT - Avoid unclear situations, preserve capital"
#         what_not_to_do = "❌ DON'T FORCE TRADES - Wait for clear institutional direction"
#         risk_management = "🛡️ No position - no risk"
#         profit_strategy = "💰 Preserve capital for better opportunities"
    
#     # Generate comprehensive anti-matrix narrative
#     anti_matrix_narrative = f"ANTI-MATRIX GAME ANALYSIS: "
#     anti_matrix_narrative += f"Game Phase: {game_phase}. "
#     anti_matrix_narrative += f"Institutional Agenda: {institutional_agenda}. "
#     anti_matrix_narrative += f"Retail Trap: {retail_trap}. "
#     anti_matrix_narrative += f"Anti-Matrix Strategy: {anti_matrix_strategy}. "
#     anti_matrix_narrative += f"Institutional Plan: {institutional_plan}. "
#     anti_matrix_narrative += f"Retail Expectation: {retail_expectation}. "
#     anti_matrix_narrative += f"Anti-Matrix Prediction: {anti_matrix_prediction}. "
#     anti_matrix_narrative += f"Next Candles: {next_candles_expectation}."
    
#     # Add confidence context
#     if confidence >= 12:
#         confidence_context = "Very strong anti-matrix conviction"
#     elif confidence >= 9:
#         confidence_context = "Strong anti-matrix conviction"
#     elif confidence >= 6:
#         confidence_context = "Moderate anti-matrix conviction"
#     else:
#         confidence_context = "Weak anti-matrix conviction"
    
#     logger.info(f"�� ANTI-MATRIX BIGGER PICTURE:")
#     logger.info(f"   Signal: {signal} | Confidence: {confidence} ({confidence_context})")
#     logger.info(f"   Game Phase: {game_phase}")
#     logger.info(f"   Institutional Agenda: {institutional_agenda}")
#     logger.info(f"   Retail Trap: {retail_trap}")
#     logger.info(f"   Anti-Matrix Strategy: {anti_matrix_strategy}")
#     logger.info(f"   Institutional Plan: {institutional_plan}")
#     logger.info(f"   Retail Expectation: {retail_expectation}")
#     logger.info(f"   Anti-Matrix Prediction: {anti_matrix_prediction}")
#     logger.info(f"   Next Candles: {next_candles_expectation}")
#     logger.info(f"   WHAT TO DO: {what_to_do}")
#     logger.info(f"   WHAT NOT TO DO: {what_not_to_do}")
#     logger.info(f"   Risk Management: {risk_management}")
#     logger.info(f"   Profit Strategy: {profit_strategy}")
#     logger.info(f"   Narrative: {anti_matrix_narrative}")
    
#     return {
#         'signal': signal,
#         'confidence': confidence,
#         'game_phase': game_phase,
#         'institutional_agenda': institutional_agenda,
#         'retail_trap': retail_trap,
#         'anti_matrix_strategy': anti_matrix_strategy,
#         'institutional_plan': institutional_plan,
#         'retail_expectation': retail_expectation,
#         'anti_matrix_prediction': anti_matrix_prediction,
#         'next_candles_expectation': next_candles_expectation,
#         'what_to_do': what_to_do,
#         'what_not_to_do': what_not_to_do,
#         'risk_management': risk_management,
#         'profit_strategy': profit_strategy,
#         'narrative': anti_matrix_narrative,
#         'confidence_context': confidence_context,
#         'candles_analyzed': len(multi_candle_reasons)
#     }

def analyze_bigger_picture(current_price, signal, confidence, reasons):
    """Independent Anti-Matrix Analysis - Creates its own signals based on comprehensive analysis with price tracking"""
    global multi_candle_reasons
    
    # Add current reasons to multi-candle tracking with price data
    multi_candle_reasons.append({
        'timestamp': time.time(),
        'signal': signal,
        'confidence': confidence,
        'reasons': reasons,
        'price': current_price  # ADDED: Track price
    })
    
    # Keep only last 10 candles
    if len(multi_candle_reasons) > 10:
        multi_candle_reasons = multi_candle_reasons[-10:]
    
    # PRICE MOVEMENT ANALYSIS (ADDED)
    price_movements = []
    price_trend = "unknown"
    price_volatility = "unknown"
    
    if len(multi_candle_reasons) >= 2:
        for i in range(1, len(multi_candle_reasons)):
            current_candle = multi_candle_reasons[i]
            previous_candle = multi_candle_reasons[i-1]
            
            if 'price' in current_candle and 'price' in previous_candle:
                price_change = current_candle['price'] - previous_candle['price']
                price_direction = 'up' if price_change > 0 else 'down'
                price_movements.append({
                    'direction': price_direction,
                    'change': price_change,
                    'signal': current_candle['signal'],
                    'reasons': current_candle['reasons']
                })
    
    # Determine price trend
    if len(price_movements) >= 3:
        recent_movements = price_movements[-3:]
        up_count = sum(1 for m in recent_movements if m['direction'] == 'up')
        down_count = sum(1 for m in recent_movements if m['direction'] == 'down')
        
        if up_count > down_count:
            price_trend = "upward trend"
        elif down_count > up_count:
            price_trend = "downward trend"
        else:
            price_trend = "sideways trend"
    
    # Determine price volatility
    if len(price_movements) >= 3:
        changes = [abs(m['change']) for m in price_movements[-3:]]
        avg_change = sum(changes) / len(changes)
        if avg_change > 0.001:  # Adjust threshold as needed
            price_volatility = "high volatility"
        elif avg_change > 0.0005:
            price_volatility = "medium volatility"
        else:
            price_volatility = "low volatility"
    
    # INDEPENDENT ANTI-MATRIX ANALYSIS
    institutional_behavior = {}
    retail_psychology = {}
    trap_identification = {}
    market_dynamics = {}
    
    # Analyze patterns across multiple candles
    all_reasons = []
    signal_trend = []
    confidence_trend = []
    
    for candle_data in multi_candle_reasons:
        all_reasons.extend(candle_data['reasons'])
        signal_trend.append(candle_data['signal'])
        confidence_trend.append(candle_data['confidence'])
    
    # COMPREHENSIVE PATTERN ANALYSIS
    for reason in all_reasons:
        reason_lower = reason.lower()
        
        # Institutional Behavior Patterns
        if 'ask layering' in reason_lower:
            institutional_behavior['ask_layering'] = institutional_behavior.get('ask_layering', 0) + 1
            trap_identification['bullish_trap'] = trap_identification.get('bullish_trap', 0) + 1
        elif 'bid layering' in reason_lower:
            institutional_behavior['bid_layering'] = institutional_behavior.get('bid_layering', 0) + 1
            trap_identification['bearish_trap'] = trap_identification.get('bearish_trap', 0) + 1
        elif 'liquidity grab' in reason_lower:
            institutional_behavior['liquidity_grab'] = institutional_behavior.get('liquidity_grab', 0) + 1
            trap_identification['stop_hunting'] = trap_identification.get('stop_hunting', 0) + 1
        elif 'stop hunting' in reason_lower:
            institutional_behavior['stop_hunting'] = institutional_behavior.get('stop_hunting', 0) + 1
            trap_identification['stop_hunting'] = trap_identification.get('stop_hunting', 0) + 1
        
        # Market Phase Patterns
        elif 'accumulation' in reason_lower:
            institutional_behavior['accumulation'] = institutional_behavior.get('accumulation', 0) + 1
            market_dynamics['institutional_buying'] = market_dynamics.get('institutional_buying', 0) + 1
        elif 'distribution' in reason_lower:
            institutional_behavior['distribution'] = institutional_behavior.get('distribution', 0) + 1
            market_dynamics['institutional_selling'] = market_dynamics.get('institutional_selling', 0) + 1
        
        # Retail Psychology Patterns
        elif 'session manipulation' in reason_lower:
            retail_psychology['manipulation'] = retail_psychology.get('manipulation', 0) + 1
        elif 'volume' in reason_lower:
            retail_psychology['volume_reaction'] = retail_psychology.get('volume_reaction', 0) + 1
    
    # PRICE-SIGNAL CORRELATION ANALYSIS (ADDED)
    signal_price_correlation = "unknown"
    pattern_accuracy = "unknown"
    
    if len(price_movements) >= 3:
        correct_predictions = 0
        total_predictions = 0
        
        for movement in price_movements[-3:]:
            if movement['signal'] == 'SHORT' and movement['direction'] == 'down':
                correct_predictions += 1
            elif movement['signal'] == 'LONG' and movement['direction'] == 'up':
                correct_predictions += 1
            total_predictions += 1
        
        if total_predictions > 0:
            accuracy_rate = correct_predictions / total_predictions
            if accuracy_rate >= 0.7:
                pattern_accuracy = "high accuracy"
            elif accuracy_rate >= 0.5:
                pattern_accuracy = "moderate accuracy"
            else:
                pattern_accuracy = "low accuracy"
    
    # INDEPENDENT GAME PHASE ANALYSIS
    game_phase = "unknown"
    institutional_agenda = "unknown"
    retail_trap = "unknown"
    
    # Determine Game Phase (Independent Analysis)
    if institutional_behavior.get('ask_layering', 0) > 2:
        game_phase = "institutional trap setting - they want retail to buy so they can dump"
    elif institutional_behavior.get('bid_layering', 0) > 2:
        game_phase = "institutional trap setting - they want retail to sell so they can pump"
    elif institutional_behavior.get('liquidity_grab', 0) > 2:
        game_phase = "institutional stop hunting - they're hunting retail stops"
    elif institutional_behavior.get('accumulation', 0) > 2:
        game_phase = "institutional accumulation - they're buying while retail is selling"
    elif institutional_behavior.get('distribution', 0) > 2:
        game_phase = "institutional distribution - they're selling while retail is buying"
    else:
        game_phase = "neutral phase - unclear institutional agenda"
    
    # Determine Institutional Agenda (Independent Analysis)
    if 'ask_layering' in game_phase:
        institutional_agenda = "institutions setting bullish traps to dump on retail"
    elif 'bid_layering' in game_phase:
        institutional_agenda = "institutions setting bearish traps to pump and dump"
    elif 'stop hunting' in game_phase:
        institutional_agenda = "institutions hunting retail stops for liquidity"
    elif 'accumulation' in game_phase:
        institutional_agenda = "institutions accumulating for a major move"
    elif 'distribution' in game_phase:
        institutional_agenda = "institutions distributing for a major move"
    else:
        institutional_agenda = "institutional agenda unclear"
    
    # Determine Retail Trap (Independent Analysis)
    if institutional_behavior.get('ask_layering', 0) > 2:
        retail_trap = "retail will see bullish signals and buy (FOMO trap)"
    elif institutional_behavior.get('bid_layering', 0) > 2:
        retail_trap = "retail will see bearish signals and sell (panic trap)"
    elif institutional_behavior.get('liquidity_grab', 0) > 2:
        retail_trap = "retail will panic sell at stops (liquidity trap)"
    else:
        retail_trap = "retail behavior unclear"
    
    # INDEPENDENT ANTI-MATRIX SIGNAL GENERATION
    anti_matrix_signal = "WAIT"
    anti_matrix_confidence = 0
    anti_matrix_strategy = "unknown"
    
    # Generate Independent Signal Based on Analysis
    if institutional_behavior.get('ask_layering', 0) > 2:
        anti_matrix_signal = "SHORT"
        anti_matrix_confidence = 8
        anti_matrix_strategy = "GO SHORT - Avoid bullish trap, catch the dump after pump"
    elif institutional_behavior.get('bid_layering', 0) > 2:
        anti_matrix_signal = "LONG"
        anti_matrix_confidence = 8
        anti_matrix_strategy = "GO LONG - Avoid bearish trap, catch the pump after dump"
    elif institutional_behavior.get('distribution', 0) > 2:
        anti_matrix_signal = "SHORT"
        anti_matrix_confidence = 7
        anti_matrix_strategy = "GO SHORT - Follow institutional selling, avoid retail buying"
    elif institutional_behavior.get('accumulation', 0) > 2:
        anti_matrix_signal = "LONG"
        anti_matrix_confidence = 7
        anti_matrix_strategy = "GO LONG - Follow institutional buying, avoid retail selling"
    elif institutional_behavior.get('liquidity_grab', 0) > 2:
        if institutional_behavior.get('bearish_bias', 0) > institutional_behavior.get('bullish_bias', 0):
            anti_matrix_signal = "SHORT"
            anti_matrix_confidence = 6
            anti_matrix_strategy = "GO SHORT - Catch institutional stop hunt"
        else:
            anti_matrix_signal = "LONG"
            anti_matrix_confidence = 6
            anti_matrix_strategy = "GO LONG - Catch institutional stop hunt"
    elif institutional_behavior.get('stop_hunting', 0) > 2:
        if institutional_behavior.get('bearish_bias', 0) > institutional_behavior.get('bullish_bias', 0):
            anti_matrix_signal = "SHORT"
            anti_matrix_confidence = 6
            anti_matrix_strategy = "GO SHORT - Catch institutional stop hunt"
        else:
            anti_matrix_signal = "LONG"
            anti_matrix_confidence = 6
            anti_matrix_strategy = "GO LONG - Catch institutional stop hunt"
    else:
        anti_matrix_signal = "WAIT"
        anti_matrix_confidence = 3
        anti_matrix_strategy = "WAIT - Avoid unclear situations, preserve capital"
    
    # INDEPENDENT PREDICTIONS
    institutional_plan = ""
    retail_expectation = ""
    anti_matrix_prediction = ""
    next_candles_expectation = ""
    
    if anti_matrix_signal == "SHORT":
        if institutional_behavior.get('ask_layering', 0) > 2:
            institutional_plan = "institutions will pump price to trap retail buyers, then dump"
            retail_expectation = "retail will FOMO buy during the pump"
            anti_matrix_prediction = "we go SHORT to catch the dump after the pump"
            next_candles_expectation = "expect pump in next 2-5 candles, dump in 5-10 candles"
        elif institutional_behavior.get('distribution', 0) > 2:
            institutional_plan = "institutions will continue selling while retail buys"
            retail_expectation = "retail will buy the dip and get trapped"
            anti_matrix_prediction = "we go SHORT with institutions against retail"
            next_candles_expectation = "expect continued downward pressure for 5-15 candles"
        elif institutional_behavior.get('liquidity_grab', 0) > 2 or institutional_behavior.get('stop_hunting', 0) > 2:
            institutional_plan = "institutions will hunt retail stops downward"
            retail_expectation = "retail will panic sell at stops"
            anti_matrix_prediction = "we go SHORT to catch the stop hunt"
            next_candles_expectation = "expect sharp downward move in next 1-3 candles"
        else:
            institutional_plan = "institutions are bearish and will push price down"
            retail_expectation = "retail may still be bullish"
            anti_matrix_prediction = "we go SHORT with institutions against retail"
            next_candles_expectation = "expect gradual downward movement over 10-20 candles"
    
    elif anti_matrix_signal == "LONG":
        if institutional_behavior.get('bid_layering', 0) > 2:
            institutional_plan = "institutions will dump price to trap retail sellers, then pump"
            retail_expectation = "retail will panic sell during the dump"
            anti_matrix_prediction = "we go LONG to catch the pump after the dump"
            next_candles_expectation = "expect dump in next 2-5 candles, pump in 5-10 candles"
        elif institutional_behavior.get('accumulation', 0) > 2:
            institutional_plan = "institutions will continue buying while retail sells"
            retail_expectation = "retail will sell the rally and get trapped"
            anti_matrix_prediction = "we go LONG with institutions against retail"
            next_candles_expectation = "expect continued upward pressure for 5-15 candles"
        elif institutional_behavior.get('liquidity_grab', 0) > 2 or institutional_behavior.get('stop_hunting', 0) > 2:
            institutional_plan = "institutions will hunt retail stops upward"
            retail_expectation = "retail will panic buy at stops"
            anti_matrix_prediction = "we go LONG to catch the stop hunt"
            next_candles_expectation = "expect sharp upward move in next 1-3 candles"
        else:
            institutional_plan = "institutions are bullish and will push price up"
            retail_expectation = "retail may still be bearish"
            anti_matrix_prediction = "we go LONG with institutions against retail"
            next_candles_expectation = "expect gradual upward movement over 10-20 candles"
    
    else:  # WAIT
        institutional_plan = "institutions are unclear, avoiding potential traps"
        retail_expectation = "retail may be confused"
        anti_matrix_prediction = "we wait for clearer institutional direction"
        next_candles_expectation = "wait 5-10 candles for clearer signals"
    
    # INDEPENDENT ACTION RECOMMENDATIONS
    what_to_do = ""
    what_not_to_do = ""
    risk_management = ""
    profit_strategy = ""
    
    if anti_matrix_signal == "SHORT":
        if institutional_behavior.get('ask_layering', 0) > 2:
            what_to_do = "✅ GO SHORT - Avoid bullish trap, catch institutional dump"
            what_not_to_do = "❌ DON'T FOMO BUY - That's exactly what institutions want"
            risk_management = "️ Use wider stops - expect pump before dump"
            profit_strategy = "💰 Target dump after pump - be patient with trap"
        elif institutional_behavior.get('distribution', 0) > 2:
            what_to_do = "✅ GO SHORT - Follow institutional selling"
            what_not_to_do = "❌ DON'T BUY THE DIP - Institutions are selling"
            risk_management = "🛡️ Use standard stops - steady downward pressure"
            profit_strategy = "💰 Target 2-3x risk reward - distribution may continue"
        elif institutional_behavior.get('liquidity_grab', 0) > 2 or institutional_behavior.get('stop_hunting', 0) > 2:
            what_to_do = "✅ GO SHORT - Catch institutional stop hunt"
            what_not_to_do = "❌ DON'T USE TIGHT STOPS - You'll get hunted"
            risk_management = "️ Use wider stops - expect volatility"
            profit_strategy = "💰 Quick profits - stop hunt may be brief"
        else:
            what_to_do = "✅ GO SHORT - Anti-matrix bearish bias"
            what_not_to_do = "❌ DON'T FIGHT INSTITUTIONS - They control the market"
            risk_management = "🛡️ Use standard stops"
            profit_strategy = "💰 Target 2x risk reward"
    
    elif anti_matrix_signal == "LONG":
        if institutional_behavior.get('bid_layering', 0) > 2:
            what_to_do = "✅ GO LONG - Avoid bearish trap, catch institutional pump"
            what_not_to_do = "❌ DON'T PANIC SELL - That's exactly what institutions want"
            risk_management = "️ Use wider stops - expect dump before pump"
            profit_strategy = "💰 Target pump after dump - be patient with trap"
        elif institutional_behavior.get('accumulation', 0) > 2:
            what_to_do = "✅ GO LONG - Follow institutional buying"
            what_not_to_do = "❌ DON'T SELL THE RALLY - Institutions are buying"
            risk_management = "🛡️ Use standard stops - steady upward pressure"
            profit_strategy = "💰 Target 2-3x risk reward - accumulation may continue"
        elif institutional_behavior.get('liquidity_grab', 0) > 2 or institutional_behavior.get('stop_hunting', 0) > 2:
            what_to_do = "✅ GO LONG - Catch institutional stop hunt"
            what_not_to_do = "❌ DON'T USE TIGHT STOPS - You'll get hunted"
            risk_management = "️ Use wider stops - expect volatility"
            profit_strategy = "💰 Quick profits - stop hunt may be brief"
        else:
            what_to_do = "✅ GO LONG - Anti-matrix bullish bias"
            what_not_to_do = "❌ DON'T FIGHT INSTITUTIONS - They control the market"
            risk_management = "🛡️ Use standard stops"
            profit_strategy = "💰 Target 2x risk reward"
    
    else:  # WAIT
        what_to_do = "✅ STAY OUT - Avoid unclear situations, preserve capital"
        what_not_to_do = "❌ DON'T FORCE TRADES - Wait for clear institutional direction"
        risk_management = "🛡️ No position - no risk"
        profit_strategy = "💰 Preserve capital for better opportunities"
    
    # SIGNAL COMPARISON ANALYSIS
    signal_comparison = ""
    if anti_matrix_signal != signal:
        signal_comparison = f"⚠️ ANTI-MATRIX SIGNAL DIFFERENT: Bot says {signal}, Anti-Matrix says {anti_matrix_signal}"
        if anti_matrix_confidence > confidence:
            signal_comparison += f" - Anti-Matrix has higher confidence ({anti_matrix_confidence} vs {confidence})"
        else:
            signal_comparison += f" - Bot has higher confidence ({confidence} vs {anti_matrix_confidence})"
    else:
        signal_comparison = f"✅ SIGNAL AGREEMENT: Both Bot and Anti-Matrix say {signal}"
    
    # Generate comprehensive anti-matrix narrative with price tracking
    anti_matrix_narrative = f"INDEPENDENT ANTI-MATRIX ANALYSIS: "
    anti_matrix_narrative += f"Game Phase: {game_phase}. "
    anti_matrix_narrative += f"Institutional Agenda: {institutional_agenda}. "
    anti_matrix_narrative += f"Retail Trap: {retail_trap}. "
    anti_matrix_narrative += f"Anti-Matrix Signal: {anti_matrix_signal} (Confidence: {anti_matrix_confidence}). "
    anti_matrix_narrative += f"Anti-Matrix Strategy: {anti_matrix_strategy}. "
    anti_matrix_narrative += f"Institutional Plan: {institutional_plan}. "
    anti_matrix_narrative += f"Retail Expectation: {retail_expectation}. "
    anti_matrix_narrative += f"Anti-Matrix Prediction: {anti_matrix_prediction}. "
    anti_matrix_narrative += f"Next Candles: {next_candles_expectation}. "
    anti_matrix_narrative += f"Signal Comparison: {signal_comparison}. "
    anti_matrix_narrative += f"Price Analysis: {price_trend}, {price_volatility}, Pattern Accuracy: {pattern_accuracy}."
    
    # Add confidence context
    if anti_matrix_confidence >= 12:
        confidence_context = "Very strong anti-matrix conviction"
    elif anti_matrix_confidence >= 9:
        confidence_context = "Strong anti-matrix conviction"
    elif anti_matrix_confidence >= 6:
        confidence_context = "Moderate anti-matrix conviction"
    else:
        confidence_context = "Weak anti-matrix conviction"
    
    logger.info(f" INDEPENDENT ANTI-MATRIX BIGGER PICTURE:")
    logger.info(f"   Bot Signal: {signal} | Bot Confidence: {confidence}")
    logger.info(f"   Anti-Matrix Signal: {anti_matrix_signal} | Anti-Matrix Confidence: {anti_matrix_confidence} ({confidence_context})")
    logger.info(f"   Game Phase: {game_phase}")
    logger.info(f"   Institutional Agenda: {institutional_agenda}")
    logger.info(f"   Retail Trap: {retail_trap}")
    logger.info(f"   Anti-Matrix Strategy: {anti_matrix_strategy}")
    logger.info(f"   Institutional Plan: {institutional_plan}")
    logger.info(f"   Retail Expectation: {retail_expectation}")
    logger.info(f"   Anti-Matrix Prediction: {anti_matrix_prediction}")
    logger.info(f"   Next Candles: {next_candles_expectation}")
    logger.info(f"   Signal Comparison: {signal_comparison}")
    logger.info(f"   Price Trend: {price_trend}")
    logger.info(f"   Price Volatility: {price_volatility}")
    logger.info(f"   Pattern Accuracy: {pattern_accuracy}")
    logger.info(f"   WHAT TO DO: {what_to_do}")
    logger.info(f"   WHAT NOT TO DO: {what_not_to_do}")
    logger.info(f"   Risk Management: {risk_management}")
    logger.info(f"   Profit Strategy: {profit_strategy}")
    logger.info(f"   Narrative: {anti_matrix_narrative}")
    
    return {
        'bot_signal': signal,
        'bot_confidence': confidence,
        'anti_matrix_signal': anti_matrix_signal,
        'anti_matrix_confidence': anti_matrix_confidence,
        'game_phase': game_phase,
        'institutional_agenda': institutional_agenda,
        'retail_trap': retail_trap,
        'anti_matrix_strategy': anti_matrix_strategy,
        'institutional_plan': institutional_plan,
        'retail_expectation': retail_expectation,
        'anti_matrix_prediction': anti_matrix_prediction,
        'next_candles_expectation': next_candles_expectation,
        'signal_comparison': signal_comparison,
        'price_trend': price_trend,
        'price_volatility': price_volatility,
        'pattern_accuracy': pattern_accuracy,
        'what_to_do': what_to_do,
        'what_not_to_do': what_not_to_do,
        'risk_management': risk_management,
        'profit_strategy': profit_strategy,
        'narrative': anti_matrix_narrative,
        'confidence_context': confidence_context,
        'candles_analyzed': len(multi_candle_reasons)
    }

def analyze_institutional_liquidity_for_reversal(multi_candle_reasons, current_price):
    """Analyze if institutions have enough liquidity for a reversal in coming candles"""
    
    # Track institutional liquidity patterns
    liquidity_analysis = {
        'institutional_liquidity': 0,
        'reversal_probability': 0,
        'reversal_timeframe': "unknown",
        'liquidity_sources': [],
        'reversal_signals': []
    }
    
    # Analyze last 10 candles for liquidity patterns
    if len(multi_candle_reasons) >= 5:
        all_reasons = []
        for candle_data in multi_candle_reasons:
            all_reasons.extend(candle_data['reasons'])
        
        # Count liquidity-related patterns
        liquidity_patterns = {
            'liquidity_grab': 0,
            'stop_hunting': 0,
            'layering': 0,
            'accumulation': 0,
            'distribution': 0,
            'volume_spikes': 0
        }
        
        for reason in all_reasons:
            reason_lower = reason.lower()
            if 'liquidity grab' in reason_lower:
                liquidity_patterns['liquidity_grab'] += 1
            elif 'stop hunting' in reason_lower:
                liquidity_patterns['stop_hunting'] += 1
            elif 'layering' in reason_lower:
                liquidity_patterns['layering'] += 1
            elif 'accumulation' in reason_lower:
                liquidity_patterns['accumulation'] += 1
            elif 'distribution' in reason_lower:
                liquidity_patterns['distribution'] += 1
            elif 'volume' in reason_lower:
                liquidity_patterns['volume_spikes'] += 1
        
        # Calculate institutional liquidity score
        liquidity_score = (
            liquidity_patterns['liquidity_grab'] * 2 +
            liquidity_patterns['stop_hunting'] * 2 +
            liquidity_patterns['layering'] * 1.5 +
            liquidity_patterns['accumulation'] * 1 +
            liquidity_patterns['distribution'] * 1 +
            liquidity_patterns['volume_spikes'] * 0.5
        )
        
        liquidity_analysis['institutional_liquidity'] = liquidity_score
        
        # Determine reversal probability
        if liquidity_score >= 8:
            liquidity_analysis['reversal_probability'] = 85
            liquidity_analysis['reversal_timeframe'] = "1-3 candles"
            liquidity_analysis['reversal_signals'].append("High institutional liquidity - reversal imminent")
        elif liquidity_score >= 5:
            liquidity_analysis['reversal_probability'] = 65
            liquidity_analysis['reversal_timeframe'] = "3-5 candles"
            liquidity_analysis['reversal_signals'].append("Moderate institutional liquidity - reversal likely")
        elif liquidity_score >= 3:
            liquidity_analysis['reversal_probability'] = 45
            liquidity_analysis['reversal_timeframe'] = "5-10 candles"
            liquidity_analysis['reversal_signals'].append("Low institutional liquidity - reversal possible")
        else:
            liquidity_analysis['reversal_probability'] = 25
            liquidity_analysis['reversal_timeframe'] = "10+ candles"
            liquidity_analysis['reversal_signals'].append("Minimal institutional liquidity - no reversal expected")
        
        # Identify liquidity sources
        if liquidity_patterns['liquidity_grab'] > 2:
            liquidity_analysis['liquidity_sources'].append("Aggressive liquidity grabbing from retail")
        if liquidity_patterns['stop_hunting'] > 2:
            liquidity_analysis['liquidity_sources'].append("Stop hunting for retail stops")
        if liquidity_patterns['layering'] > 2:
            liquidity_analysis['liquidity_sources'].append("Layering for trap setup")
        if liquidity_patterns['accumulation'] > 2:
            liquidity_analysis['liquidity_sources'].append("Accumulation phase building liquidity")
        if liquidity_patterns['distribution'] > 2:
            liquidity_analysis['liquidity_sources'].append("Distribution phase providing liquidity")
    
    return liquidity_analysis

def analyze_retail_price_pressure(multi_candle_reasons, current_price):
    """Analyze if retail is pushing price up or down"""
    
    retail_analysis = {
        'retail_direction': "unknown",
        'retail_strength': 0,
        'retail_behavior': "unknown",
        'retail_traps': [],
        'retail_sentiment': "unknown",
        'retail_vs_institutional': "unknown"
    }
    
    # Analyze retail behavior patterns
    if len(multi_candle_reasons) >= 3:
        retail_patterns = {
            'fomo_buying': 0,
            'panic_selling': 0,
            'dip_buying': 0,
            'rally_selling': 0,
            'retail_traps': 0,
            'volume_reaction': 0
        }
        
        all_reasons = []
        for candle_data in multi_candle_reasons:
            all_reasons.extend(candle_data['reasons'])
        
        # Analyze retail behavior from reasons
        for reason in all_reasons:
            reason_lower = reason.lower()
            
            # Retail FOMO patterns
            if 'fomo' in reason_lower or 'bullish trap' in reason_lower:
                retail_patterns['fomo_buying'] += 1
            elif 'panic' in reason_lower or 'bearish trap' in reason_lower:
                retail_patterns['panic_selling'] += 1
            
            # Retail dip buying/rally selling
            elif 'dip' in reason_lower and 'buy' in reason_lower:
                retail_patterns['dip_buying'] += 1
            elif 'rally' in reason_lower and 'sell' in reason_lower:
                retail_patterns['rally_selling'] += 1
            
            # Retail traps
            elif 'retail trap' in reason_lower or 'trap' in reason_lower:
                retail_patterns['retail_traps'] += 1
            
            # Volume reactions (often retail-driven)
            elif 'volume' in reason_lower and ('high' in reason_lower or 'spike' in reason_lower):
                retail_patterns['volume_reaction'] += 1
        
        # Determine retail direction
        bullish_retail = retail_patterns['fomo_buying'] + retail_patterns['dip_buying']
        bearish_retail = retail_patterns['panic_selling'] + retail_patterns['rally_selling']
        
        if bullish_retail > bearish_retail and bullish_retail > 2:
            retail_analysis['retail_direction'] = "pushing price UP"
            retail_analysis['retail_strength'] = bullish_retail
            retail_analysis['retail_behavior'] = "bullish retail sentiment"
        elif bearish_retail > bullish_retail and bearish_retail > 2:
            retail_analysis['retail_direction'] = "pushing price DOWN"
            retail_analysis['retail_strength'] = bearish_retail
            retail_analysis['retail_behavior'] = "bearish retail sentiment"
        else:
            retail_analysis['retail_direction'] = "neutral pressure"
            retail_analysis['retail_strength'] = max(bullish_retail, bearish_retail)
            retail_analysis['retail_behavior'] = "mixed retail sentiment"
        
        # Analyze retail sentiment
        if retail_patterns['retail_traps'] > 2:
            retail_analysis['retail_sentiment'] = "easily trapped - falling for institutional manipulation"
            retail_analysis['retail_traps'].append("High trap susceptibility")
        elif retail_patterns['volume_reaction'] > 2:
            retail_analysis['retail_sentiment'] = "emotional trading - reacting to volume spikes"
            retail_analysis['retail_traps'].append("Volume-driven decisions")
        else:
            retail_analysis['retail_sentiment'] = "moderate sentiment - not easily manipulated"
        
        # Compare retail vs institutional
        institutional_patterns = {
            'ask_layering': sum(1 for r in all_reasons if 'ask layering' in r.lower()),
            'bid_layering': sum(1 for r in all_reasons if 'bid layering' in r.lower()),
            'liquidity_grab': sum(1 for r in all_reasons if 'liquidity grab' in r.lower()),
            'stop_hunting': sum(1 for r in all_reasons if 'stop hunting' in r.lower())
        }
        
        institutional_strength = sum(institutional_patterns.values())
        
        if retail_analysis['retail_strength'] > institutional_strength:
            retail_analysis['retail_vs_institutional'] = "retail stronger - institutions may struggle"
        elif institutional_strength > retail_analysis['retail_strength']:
            retail_analysis['retail_vs_institutional'] = "institutions stronger - retail being controlled"
        else:
            retail_analysis['retail_vs_institutional'] = "balanced - retail and institutions equal"
    
    return retail_analysis

def predict_trend_reversal_timing(liquidity_analysis, retail_analysis):
    """Predict when trend reversal will occur based on liquidity and retail analysis"""
    
    reversal_prediction = {
        'reversal_likelihood': 0,
        'reversal_timeframe': "unknown",
        'reversal_trigger': "unknown",
        'reversal_strength': "unknown",
        'confidence': 0
    }
    
    # Combine liquidity and retail analysis
    liquidity_score = liquidity_analysis.get('institutional_liquidity', 0)
    reversal_prob = liquidity_analysis.get('reversal_probability', 0)
    retail_direction = retail_analysis.get('retail_direction', "unknown")
    retail_strength = retail_analysis.get('retail_strength', 0)
    
    # Calculate reversal likelihood
    if liquidity_score >= 8 and retail_strength >= 3:
        reversal_prediction['reversal_likelihood'] = 90
        reversal_prediction['reversal_timeframe'] = "1-2 candles"
        reversal_prediction['reversal_trigger'] = "High institutional liquidity + strong retail pressure"
        reversal_prediction['reversal_strength'] = "very strong"
        reversal_prediction['confidence'] = 9
    elif liquidity_score >= 5 and retail_strength >= 2:
        reversal_prediction['reversal_likelihood'] = 75
        reversal_prediction['reversal_timeframe'] = "2-4 candles"
        reversal_prediction['reversal_trigger'] = "Moderate institutional liquidity + retail pressure"
        reversal_prediction['reversal_strength'] = "strong"
        reversal_prediction['confidence'] = 7
    elif liquidity_score >= 3 and retail_strength >= 1:
        reversal_prediction['reversal_likelihood'] = 60
        reversal_prediction['reversal_timeframe'] = "4-7 candles"
        reversal_prediction['reversal_trigger'] = "Low institutional liquidity + weak retail pressure"
        reversal_prediction['reversal_strength'] = "moderate"
        reversal_prediction['confidence'] = 5
    else:
        reversal_prediction['reversal_likelihood'] = 30
        reversal_prediction['reversal_timeframe'] = "7+ candles"
        reversal_prediction['reversal_trigger'] = "Insufficient liquidity + weak retail pressure"
        reversal_prediction['reversal_strength'] = "weak"
        reversal_prediction['confidence'] = 3
    
    return reversal_prediction

# INTEGRATION WITH EXISTING FUNCTION (ADDED TO LOGGING)
def log_enhanced_analysis(current_price, signal, confidence, reasons):
    """Log enhanced analysis with liquidity and retail tracking"""
    
    # Get existing bigger picture analysis
    bigger_picture = analyze_bigger_picture(current_price, signal, confidence, reasons)
    
    # Add enhanced analysis
    liquidity_analysis = analyze_institutional_liquidity_for_reversal(multi_candle_reasons, current_price)
    retail_analysis = analyze_retail_price_pressure(multi_candle_reasons, current_price)
    reversal_prediction = predict_trend_reversal_timing(liquidity_analysis, retail_analysis)
    
    # Log enhanced insights
    logger.info(f" ENHANCED INSTITUTIONAL LIQUIDITY ANALYSIS:")
    logger.info(f"   Institutional Liquidity Score: {liquidity_analysis['institutional_liquidity']}")
    logger.info(f"   Reversal Probability: {liquidity_analysis['reversal_probability']}%")
    logger.info(f"   Reversal Timeframe: {liquidity_analysis['reversal_timeframe']}")
    logger.info(f"   Liquidity Sources: {', '.join(liquidity_analysis['liquidity_sources'])}")
    logger.info(f"   Reversal Signals: {', '.join(liquidity_analysis['reversal_signals'])}")
    
    logger.info(f" RETAIL PRICE PRESSURE ANALYSIS:")
    logger.info(f"   Retail Direction: {retail_analysis['retail_direction']}")
    logger.info(f"   Retail Strength: {retail_analysis['retail_strength']}")
    logger.info(f"   Retail Behavior: {retail_analysis['retail_behavior']}")
    logger.info(f"   Retail Sentiment: {retail_analysis['retail_sentiment']}")
    logger.info(f"   Retail vs Institutional: {retail_analysis['retail_vs_institutional']}")
    logger.info(f"   Retail Traps: {', '.join(retail_analysis['retail_traps'])}")
    
    logger.info(f" TREND REVERSAL PREDICTION:")
    logger.info(f"   Reversal Likelihood: {reversal_prediction['reversal_likelihood']}%")
    logger.info(f"   Reversal Timeframe: {reversal_prediction['reversal_timeframe']}")
    logger.info(f"   Reversal Trigger: {reversal_prediction['reversal_trigger']}")
    logger.info(f"   Reversal Strength: {reversal_prediction['reversal_strength']}")
    logger.info(f"   Prediction Confidence: {reversal_prediction['confidence']}/10")
    
    # Add to bigger picture return
    bigger_picture.update({
        'liquidity_analysis': liquidity_analysis,
        'retail_analysis': retail_analysis,
        'reversal_prediction': reversal_prediction
    })
    
    return bigger_picture


#=== MAIN PROCESSING FUNCTION ===
def process_candle_anti_matrix(candle):
    """Process candle with anti-matrix logic"""
    global candles, candles_dataframe, equity, current_position, session_active
    
    logger.debug(f"Processing candle: {candle}")
    
    # Check session
    session_active = is_trading_session()
    if not session_active:
        logger.debug("Outside trading session - monitoring only")
    
    candles.append(candle)
    if len(candles) > lookback:
        candles.pop(0)
        logger.debug(f"Candle buffer updated, length: {len(candles)}")
    
    if len(candles) < 20:  # Increased from 10 for robustness
        logger.debug("Insufficient candles for analysis")
        return
    
    df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = df.astype(float)
    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(timezone)
    df.set_index('timestamp', inplace=True)
    
    # Calculate price action metrics
    df['body'] = abs(df['close'] - df['open'])
    df['upper_wick'] = df['high'] - np.maximum(df['open'], df['close'])
    df['lower_wick'] = np.minimum(df['open'], df['close']) - df['low']
    df['range'] = df['high'] - df['low']

    candles_dataframe = df
    
    latest = df.iloc[-1]
    logger.debug(f"Latest candle - O: {latest['open']:.5f}, H: {latest['high']:.5f}, L: {latest['low']:.5f}, C: {latest['close']:.5f}")
    logger.debug(f"Latest candle - Body: {latest['body']:.5f}, Upper wick: {latest['upper_wick']:.5f}, Lower wick: {latest['lower_wick']:.5f}")
    
    # Update SMC components
    update_liquidity_levels(df)
    detect_fair_value_gaps(df)
    detect_order_blocks(df)
    
    # Generate anti-matrix signal
    signal, confidence, reasons = generate_anti_matrix_signal(df)
    
    # Log analysis
    logger.info(f"=== Anti-Matrix Analysis ===")
    logger.info(f"Session Active: {session_active}")
    logger.info(f"Price: {latest['close']:.5f} | Signal: {signal} | Confidence: {confidence}")
    logger.info(f"Reasons: {', '.join(reasons)}")
    
    # Analyze bigger picture across multiple candles (MINIMUM CHANGE)
    bigger_picture = analyze_bigger_picture(latest['close'], signal, confidence, reasons)
    
    log_enhanced_analysis(latest['close'], signal, confidence, reasons)

    # Execute trade only during active sessions with strategic framework
    if signal in ["LONG", "SHORT"] and session_active and current_position is None:
        # Apply strategic trading framework
        should_trade, reason = should_take_trade(signal, confidence, reasons)
        
        if should_trade:
            logger.info(f"✅ STRATEGIC TRADE APPROVED: {signal} (confidence: {confidence})")
            logger.info(f"📊 Strategy Summary: {current_strategy} active for {((time.time() - strategy_start_time) / 60):.1f} minutes")
            logger.info(f"📈 Signal History: {len(signal_history)} signals, {min_signal_consistency} required for stability")
            
            entry_price = latest['close']
            stop_loss, take_profit = calculate_adaptive_levels(signal, entry_price, df)
            
            # Calculate position size with realistic limits
            risk_amount = equity * risk_per_trade
            risk_per_share = abs(entry_price - stop_loss)
            base_position_size = risk_amount / risk_per_share
            max_position_size = max_position_value / entry_price
            position_size = min(base_position_size * leverage, max_position_size)
            
            logger.debug(f"Position sizing - Risk amount: ${risk_amount:.2f}, Risk per share: {risk_per_share:.5f}, Position size: {position_size:.2f}")
            
            if position_size > 0:
                # Execute with slippage
                fill_price = execute_with_slippage(signal, entry_price, position_size)
                
                # Calculate potential PnL (removed double leverage)
                potential_pnl = (take_profit - fill_price) * position_size if signal == "LONG" else (fill_price - take_profit) * position_size 
                
                trade = {
                    'timestamp': latest.name,
                    'signal': signal,
                    'entry': fill_price,
                    'stop_loss': stop_loss,
                    'take_profit': take_profit,
                    'position_size': position_size,
                    'confidence': confidence,
                    'reasons': reasons,
                    'potential_pnl': potential_pnl
                }
                
                current_position = trade
                trades.append(trade)
                    
                # Record the trade in strategic framework
                record_trade()
                
                logger.info(f"🚀 F&O TRADE: {symbol} {interval} - {signal} (Leverage: {leverage}x)")
                logger.info(f"   Entry: {fill_price:.5f} | SL: {stop_loss:.5f} | TP: {take_profit:.5f}")
                logger.info(f"   Position Size: {position_size:.2f} (Base: {base_position_size:.2f} × {leverage}x)")
                logger.info(f"   Potential PnL: ${potential_pnl:.2f} (Leveraged)")
                logger.info(f"   Confidence: {confidence} | Reasons: {', '.join(reasons)}")
                
                # Send telegram notification for trade entry
                telegram_msg = f"""
    🚀 *F&O TRADE EXECUTED*
    *Symbol:* {symbol.upper()} {interval}
    *Signal:* {signal}
    *Entry:* {fill_price:.5f}
    *Stop Loss:* {stop_loss:.5f}
    *Take Profit:* {take_profit:.5f}
    *Position Size:* {position_size:.2f} (Base: {base_position_size:.2f} × {leverage}x)
    *Potential PnL:* ${potential_pnl:.2f}
    *Confidence:* {confidence}
    *Reasons:* {', '.join(reasons)}
                """
                send_telegram_message(telegram_msg)
        
            else:
                logger.debug("Position size too small - skipping trade")
        else:        
            logger.info(f"❌ TRADE REJECTED: {reason}")
            logger.debug(f"Strategic Check Details - Signal: {signal}, Confidence: {confidence}, Session: {session_active}, Position: {current_position is not None}")
    else:
        logger.debug(f"Basic trade conditions not met - Signal: {signal}, Confidence: {confidence}, Session: {session_active}, Position: {current_position is not None}")

    # Check exit conditions
    if current_position:
        current_price = latest['close']
        logger.debug(f"Checking exit conditions - Current price: {current_price:.5f}, Position: {current_position['signal']}")
        
        if current_position['signal'] == "LONG":
            if current_price <= current_position['stop_loss']:
                pnl = (current_position['stop_loss'] - current_position['entry']) * current_position['position_size']  # Removed double leverage
                equity += pnl
                logger.info(f"🛑 STOP LOSS: PnL = ${pnl:.2f} | Equity = ${equity:.2f}")
                logger.debug(f"Stop loss details - Entry: {current_position['entry']:.5f}, Exit: {current_position['stop_loss']:.5f}")
                
                # Send telegram notification for stop loss
                telegram_msg = f"""
🛑 *STOP LOSS TRIGGERED*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*Exit:* {current_position['stop_loss']:.5f}
*PnL:* ${pnl:.2f}
*Equity:* ${equity:.2f}
                """
                send_telegram_message(telegram_msg)
                current_position = None
            elif current_price >= current_position['take_profit']:
                pnl = (current_position['take_profit'] - current_position['entry']) * current_position['position_size']  # Removed double leverage
                equity += pnl
                logger.info(f"🎉 TAKE PROFIT: PnL = ${pnl:.2f} | Equity = ${equity:.2f}")
                logger.debug(f"Take profit details - Entry: {current_position['entry']:.5f}, Exit: {current_position['take_profit']:.5f}")
                
                # Send telegram notification for take profit
                telegram_msg = f"""
🎉 *TAKE PROFIT HIT*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*Exit:* {current_position['take_profit']:.5f}
*PnL:* ${pnl:.2f}
*Equity:* ${equity:.2f}
                """
                send_telegram_message(telegram_msg)
                current_position = None
        else:  # SHORT
            if current_price >= current_position['stop_loss']:
                pnl = (current_position['entry'] - current_position['stop_loss']) * current_position['position_size']  # Removed double leverage
                equity += pnl
                logger.info(f"🛑 STOP LOSS: PnL = ${pnl:.2f} | Equity = ${equity:.2f}")
                logger.debug(f"Stop loss details - Entry: {current_position['entry']:.5f}, Exit: {current_position['stop_loss']:.5f}")
                
                # Send telegram notification for stop loss
                telegram_msg = f"""
🛑 *STOP LOSS TRIGGERED*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*Exit:* {current_position['stop_loss']:.5f}
*PnL:* ${pnl:.2f}
*Equity:* ${equity:.2f}
                """
                send_telegram_message(telegram_msg)
                current_position = None
            elif current_price <= current_position['take_profit']:
                pnl = (current_position['entry'] - current_position['take_profit']) * current_position['position_size']  # Removed double leverage
                equity += pnl
                logger.info(f"🎉 TAKE PROFIT: PnL = ${pnl:.2f} | Equity = ${equity:.2f}")
                logger.debug(f"Take profit details - Entry: {current_position['entry']:.5f}, Exit: {current_position['take_profit']:.5f}")
                
                # Send telegram notification for take profit
                telegram_msg = f"""
🎉 *TAKE PROFIT HIT*
*Symbol:* {symbol.upper()} {interval}
*Signal:* {current_position['signal']}
*Entry:* {current_position['entry']:.5f}
*Exit:* {current_position['take_profit']:.5f}
*PnL:* ${pnl:.2f}
*Equity:* ${equity:.2f}
                """
                send_telegram_message(telegram_msg)
                current_position = None

#=== WEBSOCKET HANDLERS ===
def on_message(ws, message):
    try:
        json_message = json.loads(message)
        
        # Skip subscription confirmation messages
        if 'result' in json_message:
            logger.debug("WebSocket subscription confirmed")
            return
        
        if 'k' in json_message:
            kline = json_message['k']
            
            # ONLY PROCESS COMPLETED CANDLES for Anti-Matrix Trading
            if kline.get('x', False):  # 'x' = True means candle is completed
                candle = [
                    int(kline['t']),
                    float(kline['o']),
                    float(kline['h']),
                    float(kline['l']),
                    float(kline['c']),
                    float(kline['v'])
                ]
                
                # Convert timestamp to readable format
                candle_time = datetime.datetime.fromtimestamp(int(kline['t'])/1000)
                
                logger.info(f"✅ COMPLETED CANDLE: {symbol.upper()} {interval} at {candle_time}")
                logger.info(f"📊 Candle Data: O:{candle[1]:.5f} H:{candle[2]:.5f} L:{candle[3]:.5f} C:{candle[4]:.5f} V:{candle[5]:.0f}")
                
                process_candle_anti_matrix(candle)
            else:
                # Log live candle updates but don't process them
                current_price = float(kline['c'])
                logger.debug(f"⏳ LIVE UPDATE: {current_price:.5f} (waiting for completion)")
                
                # REAL-TIME EXIT MONITORING FOR F&O
                if current_position:
                    check_real_time_exit_conditions(current_price)
                
    except Exception as e:
        logger.error(f"Error processing message: {e}")
        logger.error(f"Message content: {message}")

def on_open(ws):
    logger.info(f"🔌 Anti-Matrix Bot Connected: {symbol.upper()} {interval}")
    
    # Send startup notification
    telegram_msg = f"""
🤖 *ANTI-MATRIX BOT STARTED*
*Symbol:* {symbol.upper()} {interval}
*Status:* Connected and monitoring
*Equity:* ${equity:.2f}
*Risk per Trade:* {risk_per_trade*100:.1f}%
*Leverage:* {leverage}x
    """
    send_telegram_message(telegram_msg)
    payload = {"method": "SUBSCRIBE", "params": [f"{symbol}@kline_{interval}"], "id": 1}
    ws.send(json.dumps(payload))
    logger.debug("WebSocket subscription sent")

def on_error(ws, error):
    logger.error(f"WebSocket error: {error}")

def on_close(ws, close_status_code, close_msg):
    logger.info(f"WebSocket closed: {close_status_code} - {close_msg}")

def initialize_historical_data():
    """Initialize with historical data for stable analysis"""
    global candles
    
    logger.info("📊 Loading historical data for stable analysis...")
    
    try:
        # Fetch historical klines from Binance
        historical_klines = client.get_klines(
            symbol=symbol.upper(),
            interval=interval,
            limit=lookback
        )
        
        # Convert to our candle format
        for kline in historical_klines:
            candle = [
                int(kline[0]),  # timestamp
                float(kline[1]), # open
                float(kline[2]), # high
                float(kline[3]), # low
                float(kline[4]), # close
                float(kline[5])  # volume
            ]
            candles.append(candle)
        
        logger.info(f"✅ Loaded {len(candles)} historical candles")
        logger.info(f"📈 Historical range: {datetime.datetime.fromtimestamp(candles[0][0]/1000)} to {datetime.datetime.fromtimestamp(candles[-1][0]/1000)}")
        
        # Process the last few candles to initialize SMC components
        if len(candles) >= 20:
            try:
                df = pd.DataFrame(candles, columns=["timestamp", "open", "high", "low", "close", "volume"])
                df = df.astype(float)
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms').dt.tz_localize('UTC').dt.tz_convert(timezone)
                df.set_index('timestamp', inplace=True)
                
                # Calculate price action metrics for SMC
                df['body'] = abs(df['close'] - df['open'])
                df['upper_wick'] = df['high'] - np.maximum(df['open'], df['close'])
                df['lower_wick'] = np.minimum(df['open'], df['close']) - df['low']
                df['range'] = df['high'] - df['low']
                
                # Initialize SMC components
                update_liquidity_levels(df)
                detect_fair_value_gaps(df)
                detect_order_blocks(df)
                
                logger.info("✅ SMC components initialized with historical data")
            except Exception as e:
                logger.error(f"❌ Error initializing SMC components: {e}")
                logger.info("⚠️ Continuing without SMC initialization")
        
    except Exception as e:
        logger.error(f"❌ Error loading historical data: {e}")
        logger.info("⚠️ Continuing without historical data")

def run_websocket():
    """Run the anti-matrix trading bot"""
    logger.info("🚀 Starting Anti-Matrix Trading Bot")
    logger.info(f"Configuration: Symbol={symbol}, Interval={interval}")
    logger.info(f"Initial Equity: ${initial_equity}")
    logger.info("Features: SMC, Order Flow, Liquidity Tracking, Session Management")
    
    # Initialize with historical data first
    initialize_historical_data()
    
    # Start real-time order book streaming
    start_real_time_order_book_streaming()
    
    # Use WebSocket for real-time completed candles
    url = f"wss://stream.binance.com:9443/ws/{symbol}@kline_{interval}"
    logger.debug(f"Connecting to WebSocket: {url}")
    ws = websocket.WebSocketApp(url, on_open=on_open, on_message=on_message, on_error=on_error, on_close=on_close)
    ws.run_forever()

#=== RUN ===
if __name__ == "__main__":
    logger.info("🚀 Starting Anti-Matrix Trading Bot")
    logger.info(f"Configuration: Symbol={symbol}, Interval={interval}, Lookback={lookback}")
    run_websocket()
