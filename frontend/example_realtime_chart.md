# Real-Time Chart Integration Examples

This guide shows how to integrate MT5 Market Data Engine with React and TradingView widgets.

## Table of Contents

1. [Basic WebSocket Connection](#basic-websocket-connection)
2. [React Integration with Hooks](#react-integration-with-hooks)
3. [TradingView Widget Integration](#tradingview-widget-integration)
4. [Comparing MT5 Feed vs TradingView](#comparing-mt5-feed-vs-tradingview)
5. [Overlaying MT5 Data on TradingView Chart](#overlaying-mt5-data-on-tradingview-chart)

---

## Basic WebSocket Connection

### Vanilla JavaScript Example

```javascript
// Connect to tick stream
const ws = new WebSocket("ws://localhost:8000/ws/ticks/EURUSD");

ws.onopen = () => {
    console.log("Connected to MT5 tick stream");
};

ws.onmessage = (event) => {
    // Handle line-delimited JSON
    const lines = event.data.split('\n').filter(line => line.trim());
    lines.forEach(line => {
        const tick = JSON.parse(line);
        console.log("MT5 Tick:", tick);
        // tick contains: time, bid, ask, mid, spread, velocity, acceleration,
        // vol_5s, vol_30s, vol_1m, direction_bias, session, tick_count
    });
};

ws.onerror = (error) => {
    console.error("WebSocket error:", error);
};

ws.onclose = () => {
    console.log("WebSocket closed");
};
```

### Using the marketDataClient.js

```javascript
import { connectTickStream, connectCandleStream, connectMarketState } from './marketDataClient';

// Connect to tick stream
const tickWs = connectTickStream('EURUSD', (tick) => {
    console.log('Tick received:', tick);
    // Update your UI with tick data
});

// Connect to candle stream
const candleWs = connectCandleStream('EURUSD', '1s', (candle) => {
    console.log('Candle received:', candle);
    // Update your chart with candle data
});

// Connect to market state
const marketWs = connectMarketState('EURUSD', (state) => {
    console.log('Market state:', state);
    // Update dashboard with aggregated metrics
});

// Cleanup when done
// tickWs.close();
// candleWs.close();
// marketWs.close();
```

---

## React Integration with Hooks

### Using React Hooks

```jsx
import React, { useState, useEffect } from 'react';
import { useTickStream, useCandleStream, useMarketState } from './marketDataClient';

function TradingDashboard({ symbol = 'EURUSD' }) {
    // Tick stream hook
    const { tick, error: tickError, connected: tickConnected } = useTickStream(symbol);
    
    // Candle stream hook
    const { candle, error: candleError, connected: candleConnected } = useCandleStream(symbol, '1s');
    
    // Market state hook
    const { marketState, error: stateError, connected: stateConnected } = useMarketState(symbol);
    
    return (
        <div>
            <h1>Trading Dashboard - {symbol}</h1>
            
            {/* Connection Status */}
            <div>
                <p>Tick Stream: {tickConnected ? '✅ Connected' : '❌ Disconnected'}</p>
                <p>Candle Stream: {candleConnected ? '✅ Connected' : '❌ Disconnected'}</p>
                <p>Market State: {stateConnected ? '✅ Connected' : '❌ Disconnected'}</p>
            </div>
            
            {/* Latest Tick */}
            {tick && (
                <div>
                    <h2>Latest Tick</h2>
                    <p>Bid: {tick.bid.toFixed(5)}</p>
                    <p>Ask: {tick.ask.toFixed(5)}</p>
                    <p>Mid: {tick.mid.toFixed(5)}</p>
                    <p>Spread: {tick.spread.toFixed(5)}</p>
                    <p>Velocity: {tick.velocity.toFixed(6)}</p>
                    <p>Volatility (5s): {tick.vol_5s.toFixed(6)}</p>
                    <p>Direction: {tick.direction_bias > 0 ? '📈 Bullish' : '📉 Bearish'}</p>
                    <p>Session: {tick.session}</p>
                </div>
            )}
            
            {/* Latest Candle */}
            {candle && (
                <div>
                    <h2>Latest Candle ({candle.interval})</h2>
                    <p>Open: {candle.open.toFixed(5)}</p>
                    <p>High: {candle.high.toFixed(5)}</p>
                    <p>Low: {candle.low.toFixed(5)}</p>
                    <p>Close: {candle.close.toFixed(5)}</p>
                    <p>VWAP: {candle.vwap.toFixed(5)}</p>
                    <p>Volatility: {candle.volatility.toFixed(6)}</p>
                    <p>Velocity: {candle.velocity.toFixed(6)}</p>
                </div>
            )}
            
            {/* Market State */}
            {marketState && (
                <div>
                    <h2>Market State</h2>
                    <p>Trend: {marketState.trend}</p>
                    <p>Momentum: {marketState.momentum.toFixed(2)}</p>
                    <p>Volatility Rank: {(marketState.volatility_rank * 100).toFixed(1)}%</p>
                    <p>Spread: {marketState.spread.toFixed(5)}</p>
                </div>
            )}
            
            {/* Errors */}
            {(tickError || candleError || stateError) && (
                <div style={{ color: 'red' }}>
                    <p>Error: {tickError || candleError || stateError}</p>
                </div>
            )}
        </div>
    );
}

export default TradingDashboard;
```

### Custom Hook for Chart Data

```jsx
import { useState, useEffect, useRef } from 'react';
import { connectCandleStream } from './marketDataClient';

function useCandleChart(symbol, interval, maxCandles = 100) {
    const [candles, setCandles] = useState([]);
    const wsRef = useRef(null);
    
    useEffect(() => {
        if (!symbol || !interval) return;
        
        wsRef.current = connectCandleStream(symbol, interval, (candle) => {
            setCandles(prev => {
                const newCandles = [...prev, candle];
                // Keep only last N candles
                return newCandles.slice(-maxCandles);
            });
        });
        
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [symbol, interval, maxCandles]);
    
    return candles;
}

// Usage
function ChartComponent({ symbol }) {
    const candles = useCandleChart(symbol, '1s', 100);
    
    // Use candles with your charting library (Chart.js, Recharts, etc.)
    return <div>{/* Your chart component */}</div>;
}
```

---

## TradingView Widget Integration

### Basic TradingView Widget Setup

```jsx
import React, { useEffect, useRef } from 'react';

function TradingViewWidget({ symbol = 'EURUSD' }) {
    const containerRef = useRef(null);
    const widgetRef = useRef(null);
    
    useEffect(() => {
        if (!containerRef.current) return;
        
        // Create TradingView widget
        widgetRef.current = new window.TradingView.widget({
            autosize: true,
            symbol: `FX:${symbol}`,
            interval: '1',
            timezone: 'Etc/UTC',
            theme: 'dark',
            style: '1',
            locale: 'en',
            toolbar_bg: '#f1f3f6',
            enable_publishing: false,
            hide_top_toolbar: true,
            hide_legend: false,
            save_image: false,
            container_id: containerRef.current.id,
        });
        
        return () => {
            if (widgetRef.current) {
                widgetRef.current.remove();
            }
        };
    }, [symbol]);
    
    return (
        <div>
            <div id="tradingview-widget" ref={containerRef} style={{ height: '500px' }} />
        </div>
    );
}

export default TradingViewWidget;
```

### Add TradingView Script to HTML

```html
<!-- In your index.html or _document.js -->
<script type="text/javascript" src="https://s3.tradingview.com/tv.js"></script>
```

---

## Comparing MT5 Feed vs TradingView

### Side-by-Side Comparison Component

```jsx
import React, { useState, useEffect } from 'react';
import { useTickStream } from './marketDataClient';

function MT5vsTradingView({ symbol = 'EURUSD' }) {
    const { tick } = useTickStream(symbol);
    const [tradingViewPrice, setTradingViewPrice] = useState(null);
    const [priceDiff, setPriceDiff] = useState(null);
    
    // Simulate TradingView price (replace with actual TradingView API if available)
    useEffect(() => {
        // In production, connect to TradingView's WebSocket or API
        // For demo, we'll use a mock
        const interval = setInterval(() => {
            // Mock TradingView price
            const mockPrice = tick ? tick.mid + (Math.random() - 0.5) * 0.0001 : null;
            setTradingViewPrice(mockPrice);
        }, 1000);
        
        return () => clearInterval(interval);
    }, [tick]);
    
    // Calculate price difference
    useEffect(() => {
        if (tick && tradingViewPrice) {
            const diff = Math.abs(tick.mid - tradingViewPrice);
            setPriceDiff(diff);
        }
    }, [tick, tradingViewPrice]);
    
    return (
        <div style={{ display: 'flex', gap: '20px' }}>
            {/* MT5 Data */}
            <div style={{ flex: 1, border: '1px solid #ccc', padding: '20px' }}>
                <h2>MT5 Feed</h2>
                {tick ? (
                    <div>
                        <p><strong>Bid:</strong> {tick.bid.toFixed(5)}</p>
                        <p><strong>Ask:</strong> {tick.ask.toFixed(5)}</p>
                        <p><strong>Mid:</strong> {tick.mid.toFixed(5)}</p>
                        <p><strong>Spread:</strong> {tick.spread.toFixed(5)}</p>
                        <p><strong>Velocity:</strong> {tick.velocity.toFixed(6)}</p>
                        <p><strong>Session:</strong> {tick.session}</p>
                        <p><strong>Time:</strong> {new Date(tick.time).toLocaleTimeString()}</p>
                    </div>
                ) : (
                    <p>Waiting for data...</p>
                )}
            </div>
            
            {/* TradingView Data */}
            <div style={{ flex: 1, border: '1px solid #ccc', padding: '20px' }}>
                <h2>TradingView Feed</h2>
                {tradingViewPrice ? (
                    <div>
                        <p><strong>Price:</strong> {tradingViewPrice.toFixed(5)}</p>
                        <p><strong>Source:</strong> TradingView Widget</p>
                    </div>
                ) : (
                    <p>Waiting for data...</p>
                )}
            </div>
            
            {/* Comparison */}
            <div style={{ flex: 1, border: '1px solid #ccc', padding: '20px' }}>
                <h2>Comparison</h2>
                {priceDiff !== null ? (
                    <div>
                        <p><strong>Price Difference:</strong> {priceDiff.toFixed(6)}</p>
                        <p style={{ 
                            color: priceDiff > 0.0001 ? 'red' : 'green',
                            fontWeight: 'bold'
                        }}>
                            {priceDiff > 0.0001 ? '⚠️ Significant Difference' : '✅ Prices Aligned'}
                        </p>
                    </div>
                ) : (
                    <p>Calculating...</p>
                )}
            </div>
        </div>
    );
}

export default MT5vsTradingView;
```

---

## Overlaying MT5 Data on TradingView Chart

### Overlay MT5 Mid Price on TradingView

```jsx
import React, { useEffect, useRef } from 'react';
import { useTickStream } from './marketDataClient';

function TradingViewWithMT5Overlay({ symbol = 'EURUSD' }) {
    const containerRef = useRef(null);
    const widgetRef = useRef(null);
    const { tick } = useTickStream(symbol);
    
    useEffect(() => {
        if (!containerRef.current) return;
        
        widgetRef.current = new window.TradingView.widget({
            autosize: true,
            symbol: `FX:${symbol}`,
            interval: '1',
            timezone: 'Etc/UTC',
            theme: 'dark',
            style: '1',
            locale: 'en',
            container_id: containerRef.current.id,
            // Enable custom indicators
            studies_overrides: {},
            overrides: {
                // Customize chart appearance
            },
        });
        
        return () => {
            if (widgetRef.current) {
                widgetRef.current.remove();
            }
        };
    }, [symbol]);
    
    // Overlay MT5 mid price as a custom indicator
    useEffect(() => {
        if (!tick || !widgetRef.current) return;
        
        // Access TradingView's chart API to add custom line
        // Note: This requires TradingView's advanced charting library
        // For basic overlay, you can use a separate overlay component
        
    }, [tick]);
    
    return (
        <div style={{ position: 'relative' }}>
            {/* TradingView Widget */}
            <div id="tradingview-widget" ref={containerRef} style={{ height: '500px' }} />
            
            {/* MT5 Overlay Info */}
            {tick && (
                <div style={{
                    position: 'absolute',
                    top: '10px',
                    right: '10px',
                    background: 'rgba(0, 0, 0, 0.7)',
                    color: 'white',
                    padding: '10px',
                    borderRadius: '5px',
                    fontSize: '12px'
                }}>
                    <div><strong>MT5 Mid:</strong> {tick.mid.toFixed(5)}</div>
                    <div><strong>Spread:</strong> {tick.spread.toFixed(5)}</div>
                    <div><strong>Velocity:</strong> {tick.velocity.toFixed(6)}</div>
                    <div><strong>Session:</strong> {tick.session}</div>
                </div>
            )}
        </div>
    );
}

export default TradingViewWithMT5Overlay;
```

### Alternative: Separate Overlay Component

```jsx
import React, { useEffect, useRef } from 'react';
import { useTickStream } from './marketDataClient';

function MT5PriceOverlay({ symbol }) {
    const { tick } = useTickStream(symbol);
    const canvasRef = useRef(null);
    
    useEffect(() => {
        if (!tick || !canvasRef.current) return;
        
        const canvas = canvasRef.current;
        const ctx = canvas.getContext('2d');
        
        // Draw MT5 mid price line on canvas overlay
        // This would require chart coordinate mapping
        // For simplicity, use a library like Chart.js or Recharts
        
    }, [tick]);
    
    return (
        <canvas
            ref={canvasRef}
            style={{
                position: 'absolute',
                top: 0,
                left: 0,
                pointerEvents: 'none',
                zIndex: 10
            }}
        />
    );
}
```

---

## Complete Example: Full Trading Dashboard

```jsx
import React from 'react';
import { useTickStream, useCandleStream, useMarketState } from './marketDataClient';
import TradingViewWidget from './TradingViewWidget';

function FullTradingDashboard({ symbol = 'EURUSD' }) {
    const { tick } = useTickStream(symbol);
    const { candle } = useCandleStream(symbol, '1s');
    const { marketState } = useMarketState(symbol);
    
    return (
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: '20px' }}>
            {/* Left Column: TradingView Chart */}
            <div>
                <TradingViewWidget symbol={symbol} />
            </div>
            
            {/* Right Column: MT5 Metrics */}
            <div>
                <h2>MT5 Real-Time Metrics</h2>
                
                {/* Market State Summary */}
                {marketState && (
                    <div style={{ border: '1px solid #ccc', padding: '15px', marginBottom: '20px' }}>
                        <h3>Market State</h3>
                        <p>Trend: <strong>{marketState.trend}</strong></p>
                        <p>Momentum: {marketState.momentum.toFixed(2)}</p>
                        <p>Volatility Rank: {(marketState.volatility_rank * 100).toFixed(1)}%</p>
                    </div>
                )}
                
                {/* Latest Tick */}
                {tick && (
                    <div style={{ border: '1px solid #ccc', padding: '15px', marginBottom: '20px' }}>
                        <h3>Latest Tick</h3>
                        <p>Bid/Ask: {tick.bid.toFixed(5)} / {tick.ask.toFixed(5)}</p>
                        <p>Mid: {tick.mid.toFixed(5)}</p>
                        <p>Spread: {tick.spread.toFixed(5)}</p>
                        <p>Velocity: {tick.velocity > 0 ? '📈' : '📉'} {tick.velocity.toFixed(6)}</p>
                        <p>Session: {tick.session}</p>
                    </div>
                )}
                
                {/* Latest Candle */}
                {candle && (
                    <div style={{ border: '1px solid #ccc', padding: '15px' }}>
                        <h3>Latest Candle ({candle.interval})</h3>
                        <p>OHLC: {candle.open.toFixed(5)} / {candle.high.toFixed(5)} / {candle.low.toFixed(5)} / {candle.close.toFixed(5)}</p>
                        <p>VWAP: {candle.vwap.toFixed(5)}</p>
                        <p>Volatility: {candle.volatility.toFixed(6)}</p>
                    </div>
                )}
            </div>
        </div>
    );
}

export default FullTradingDashboard;
```

---

## Notes

1. **WebSocket URL**: Update `WS_BASE_URL` in `marketDataClient.js` or set `REACT_APP_WS_URL` environment variable.

2. **TradingView Widget**: Requires TradingView account and script. Free version available with limitations.

3. **Error Handling**: Always implement proper error handling and reconnection logic (already included in `marketDataClient.js`).

4. **Performance**: For high-frequency updates, consider throttling or debouncing UI updates.

5. **Data Format**: All WebSocket messages are line-delimited JSON. Handle multiple messages per event.

---

## Quick Start

1. Install dependencies:
```bash
npm install react react-dom
```

2. Import the client:
```javascript
import { connectTickStream } from './marketDataClient';
```

3. Connect and use:
```javascript
const ws = connectTickStream('EURUSD', (tick) => {
    console.log('Tick:', tick);
});
```

For more details, see the main README.md file.




