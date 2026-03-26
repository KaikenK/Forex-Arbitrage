"""
Example Usage of TradingView WebSocket Client

Multiple examples showing how to use the client.
"""

from tradingview_ws import TradingViewWS, TickData
import time

# ============================================================================
# Example 1: Basic Usage - Single Symbol
# ============================================================================

def example1_basic():
    """Basic usage with single symbol."""
    print("Example 1: Basic Usage")
    print("-" * 60)
    
    def on_tick(tick: TickData):
        print(f"{tick.symbol}: bid={tick.bid}, ask={tick.ask}, last={tick.last}")
    
    client = TradingViewWS(
        symbol="FX_IDC:EURUSD",
        on_tick=on_tick
    )
    
    client.start()
    
    try:
        time.sleep(10)  # Run for 10 seconds
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


# ============================================================================
# Example 2: Multiple Symbols
# ============================================================================

def example2_multiple_symbols():
    """Subscribe to multiple symbols."""
    print("Example 2: Multiple Symbols")
    print("-" * 60)
    
    def on_tick(tick: TickData):
        spread = (tick.ask - tick.bid) if (tick.ask and tick.bid) else None
        print(f"{tick.symbol}: {tick.last} (spread: {spread})")
    
    client = TradingViewWS(
        symbols=["FX_IDC:EURUSD", "FX_IDC:GBPUSD", "FX_IDC:USDJPY"],
        on_tick=on_tick
    )
    
    client.start()
    
    try:
        time.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


# ============================================================================
# Example 3: Custom Processing
# ============================================================================

def example3_custom_processing():
    """Custom tick processing with statistics."""
    print("Example 3: Custom Processing")
    print("-" * 60)
    
    tick_count = {}
    last_prices = {}
    
    def on_tick(tick: TickData):
        symbol = tick.symbol
        
        # Count ticks
        tick_count[symbol] = tick_count.get(symbol, 0) + 1
        
        # Track price changes
        if symbol in last_prices:
            change = tick.last - last_prices[symbol] if tick.last and last_prices[symbol] else None
            if change:
                print(f"{symbol}: {tick.last} (change: {change:+.5f}) [ticks: {tick_count[symbol]}]")
        else:
            print(f"{symbol}: {tick.last} [initial]")
        
        last_prices[symbol] = tick.last
    
    client = TradingViewWS(
        symbol="FX_IDC:EURUSD",
        on_tick=on_tick
    )
    
    client.start()
    
    try:
        time.sleep(30)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()
        print(f"\nTotal ticks received: {sum(tick_count.values())}")


# ============================================================================
# Example 4: Test Mode
# ============================================================================

def example4_test_mode():
    """Test mode - throttled to 1 tick per second."""
    print("Example 4: Test Mode (1 tick/second)")
    print("-" * 60)
    
    def on_tick(tick: TickData):
        print(f"{tick.symbol}: {tick.to_dict()}")
    
    client = TradingViewWS(
        symbol="FX_IDC:EURUSD",
        on_tick=on_tick,
        test_mode=True  # Limits to 1 tick per second
    )
    
    client.start()
    
    try:
        time.sleep(20)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


# ============================================================================
# Example 5: Dictionary Format
# ============================================================================

def example5_dict_format():
    """Use dictionary format for ticks."""
    print("Example 5: Dictionary Format")
    print("-" * 60)
    
    def on_tick(tick: TickData):
        # Convert to dictionary
        tick_dict = tick.to_dict()
        print(tick_dict)
    
    client = TradingViewWS(
        symbol="FX_IDC:EURUSD",
        on_tick=on_tick
    )
    
    client.start()
    
    try:
        time.sleep(10)
    except KeyboardInterrupt:
        pass
    finally:
        client.stop()


if __name__ == "__main__":
    import sys
    
    if len(sys.argv) > 1:
        example_num = int(sys.argv[1])
    else:
        example_num = 1
    
    examples = {
        1: example1_basic,
        2: example2_multiple_symbols,
        3: example3_custom_processing,
        4: example4_test_mode,
        5: example5_dict_format
    }
    
    if example_num in examples:
        examples[example_num]()
    else:
        print("Available examples: 1-5")
        print("Usage: python example_usage.py <example_number>")




