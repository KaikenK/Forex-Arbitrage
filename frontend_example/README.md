# Frontend Examples

## Candle Dashboard

**File:** `candle_dashboard.html`

A comprehensive real-time candle streaming dashboard with:

- **Multi-Symbol Support**: Open multiple symbols in tabs
- **Real-Time Sparkline**: 60-second price chart using Chart.js
- **Performance Diagnostics**: Latency, tick intervals, loss detection
- **TradingView-like UI**: Clean, modern, professional design
- **Color Coding**: Green for up, red for down
- **Update Highlights**: Subtle fade animations on new data
- **Auto-Scrolling Chart**: Tape-style price visualization

### Features

1. **Connection Status**
   - Visual indicator (green dot when connected)
   - Latency measurement
   - Connection/disconnection handling

2. **Performance Metrics**
   - Last message time delta
   - Average tick interval
   - Tick loss detection
   - Candle count

3. **Sparkline Chart**
   - Last 60 seconds of price data
   - Auto-scaling
   - Real-time updates
   - No animation (for performance)

4. **Data Table**
   - All candle fields (OHLC, volume, VWAP, etc.)
   - Color-coded direction
   - Highlight on update
   - Sticky header

### Usage

1. Open `candle_dashboard.html` in your browser
2. Select symbol and interval
3. Click "Connect"
4. Watch real-time data stream

### Adding More Symbols

Click the `+ SYMBOL` buttons in the header to add more tabs.

### Requirements

- Modern browser with WebSocket support
- Chart.js (loaded via CDN)
- Backend server running on `localhost:8000`




