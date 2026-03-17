/**
 * Market Data WebSocket Client
 * 
 * Reusable JavaScript client for connecting to MT5 Market Data Engine WebSocket endpoints.
 * Provides auto-reconnect, exponential backoff, and event-based API for React components.
 */

const WS_BASE_URL = process.env.REACT_APP_WS_URL || 'ws://localhost:8000';

/**
 * Base WebSocket client with auto-reconnect and exponential backoff
 */
class MarketDataWebSocket {
    constructor(url, options = {}) {
        this.url = url;
        this.options = {
            reconnectInterval: options.reconnectInterval || 1000,
            maxReconnectInterval: options.maxReconnectInterval || 30000,
            reconnectDecay: options.reconnectDecay || 1.5,
            maxReconnectAttempts: options.maxReconnectAttempts || Infinity,
            ...options
        };
        
        this.ws = null;
        this.reconnectAttempts = 0;
        this.reconnectTimer = null;
        this.listeners = {
            open: [],
            close: [],
            error: [],
            message: [],
            reconnect: []
        };
        
        this.connect();
    }
    
    connect() {
        try {
            this.ws = new WebSocket(this.url);
            
            this.ws.onopen = (event) => {
                this.reconnectAttempts = 0;
                this.emit('open', event);
            };
            
            this.ws.onclose = (event) => {
                this.emit('close', event);
                this.scheduleReconnect();
            };
            
            this.ws.onerror = (error) => {
                this.emit('error', error);
            };
            
            this.ws.onmessage = (event) => {
                try {
                    // Handle line-delimited JSON
                    const lines = event.data.split('\n').filter(line => line.trim());
                    lines.forEach(line => {
                        const data = JSON.parse(line);
                        this.emit('message', data);
                    });
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
                }
            };
        } catch (error) {
            console.error('Error creating WebSocket:', error);
            this.scheduleReconnect();
        }
    }
    
    scheduleReconnect() {
        if (this.reconnectAttempts >= this.options.maxReconnectAttempts) {
            console.error('Max reconnect attempts reached');
            return;
        }
        
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
        }
        
        const delay = Math.min(
            this.options.reconnectInterval * Math.pow(this.options.reconnectDecay, this.reconnectAttempts),
            this.options.maxReconnectInterval
        );
        
        this.reconnectAttempts++;
        this.emit('reconnect', { attempt: this.reconnectAttempts, delay });
        
        this.reconnectTimer = setTimeout(() => {
            this.connect();
        }, delay);
    }
    
    on(event, callback) {
        if (this.listeners[event]) {
            this.listeners[event].push(callback);
        }
    }
    
    off(event, callback) {
        if (this.listeners[event]) {
            this.listeners[event] = this.listeners[event].filter(cb => cb !== callback);
        }
    }
    
    emit(event, data) {
        if (this.listeners[event]) {
            this.listeners[event].forEach(callback => {
                try {
                    callback(data);
                } catch (error) {
                    console.error(`Error in ${event} listener:`, error);
                }
            });
        }
    }
    
    close() {
        if (this.reconnectTimer) {
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
        }
        
        if (this.ws) {
            this.ws.close();
            this.ws = null;
        }
    }
    
    send(data) {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) {
            this.ws.send(data);
        }
    }
}

/**
 * Connect to tick stream for a symbol
 * 
 * @param {string} symbol - Symbol to stream (e.g., "EURUSD")
 * @param {function} onData - Callback function(data) called on each tick
 * @param {object} options - WebSocket options
 * @returns {MarketDataWebSocket} WebSocket client instance
 */
export function connectTickStream(symbol, onData, options = {}) {
    const url = `${WS_BASE_URL}/ws/ticks/${symbol}`;
    const ws = new MarketDataWebSocket(url, options);
    
    ws.on('message', (data) => {
        if (data.type === 'tick' && data.symbol === symbol) {
            onData(data);
        }
    });
    
    ws.on('error', (error) => {
        console.error(`Tick stream error for ${symbol}:`, error);
    });
    
    ws.on('reconnect', ({ attempt, delay }) => {
        console.log(`Reconnecting tick stream for ${symbol} (attempt ${attempt}, delay ${delay}ms)`);
    });
    
    return ws;
}

/**
 * Connect to candle stream for a symbol and interval
 * 
 * @param {string} symbol - Symbol to stream (e.g., "EURUSD")
 * @param {string} interval - Interval (e.g., "1s", "5s", "1m", "100ms", "500ms")
 * @param {function} onData - Callback function(data) called on each candle update
 * @param {object} options - WebSocket options
 * @returns {MarketDataWebSocket} WebSocket client instance
 */
export function connectCandleStream(symbol, interval, onData, options = {}) {
    const url = `${WS_BASE_URL}/ws/candles/${symbol}/${interval}`;
    const ws = new MarketDataWebSocket(url, options);
    
    ws.on('message', (data) => {
        if (data.type === 'candle' && data.symbol === symbol && data.interval === interval) {
            onData(data);
        }
    });
    
    ws.on('error', (error) => {
        console.error(`Candle stream error for ${symbol} @ ${interval}:`, error);
    });
    
    ws.on('reconnect', ({ attempt, delay }) => {
        console.log(`Reconnecting candle stream for ${symbol} @ ${interval} (attempt ${attempt}, delay ${delay}ms)`);
    });
    
    return ws;
}

/**
 * Connect to market state stream for a symbol
 * 
 * @param {string} symbol - Symbol to stream (e.g., "EURUSD")
 * @param {function} onData - Callback function(data) called on each market state update
 * @param {object} options - WebSocket options
 * @returns {MarketDataWebSocket} WebSocket client instance
 */
export function connectMarketState(symbol, onData, options = {}) {
    const url = `${WS_BASE_URL}/ws/market/${symbol}`;
    const ws = new MarketDataWebSocket(url, options);
    
    ws.on('message', (data) => {
        if (data.type === 'market_state' && data.symbol === symbol) {
            onData(data);
        }
    });
    
    ws.on('error', (error) => {
        console.error(`Market state stream error for ${symbol}:`, error);
    });
    
    ws.on('reconnect', ({ attempt, delay }) => {
        console.log(`Reconnecting market state stream for ${symbol} (attempt ${attempt}, delay ${delay}ms)`);
    });
    
    return ws;
}

/**
 * React Hook for tick stream
 * Requires React to be available
 * 
 * @param {string} symbol - Symbol to stream
 * @param {object} options - WebSocket options
 * @returns {object} { tick, error, connected }
 */
export function useTickStream(symbol, options = {}) {
    if (typeof React === 'undefined') {
        throw new Error('React is required for useTickStream hook');
    }
    
    const [tick, setTick] = React.useState(null);
    const [error, setError] = React.useState(null);
    const [connected, setConnected] = React.useState(false);
    const wsRef = React.useRef(null);
    
    React.useEffect(() => {
        if (!symbol) return;
        
        wsRef.current = connectTickStream(
            symbol,
            (data) => {
                setTick(data);
                setError(null);
            },
            options
        );
        
        wsRef.current.on('open', () => setConnected(true));
        wsRef.current.on('close', () => setConnected(false));
        wsRef.current.on('error', (err) => setError(err));
        
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [symbol]);
    
    return { tick, error, connected };
}

/**
 * React Hook for candle stream
 * Requires React to be available
 * 
 * @param {string} symbol - Symbol to stream
 * @param {string} interval - Interval (e.g., "1s", "1m")
 * @param {object} options - WebSocket options
 * @returns {object} { candle, error, connected }
 */
export function useCandleStream(symbol, interval, options = {}) {
    if (typeof React === 'undefined') {
        throw new Error('React is required for useCandleStream hook');
    }
    
    const [candle, setCandle] = React.useState(null);
    const [error, setError] = React.useState(null);
    const [connected, setConnected] = React.useState(false);
    const wsRef = React.useRef(null);
    
    React.useEffect(() => {
        if (!symbol || !interval) return;
        
        wsRef.current = connectCandleStream(
            symbol,
            interval,
            (data) => {
                setCandle(data);
                setError(null);
            },
            options
        );
        
        wsRef.current.on('open', () => setConnected(true));
        wsRef.current.on('close', () => setConnected(false));
        wsRef.current.on('error', (err) => setError(err));
        
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [symbol, interval]);
    
    return { candle, error, connected };
}

/**
 * React Hook for market state stream
 * Requires React to be available
 * 
 * @param {string} symbol - Symbol to stream
 * @param {object} options - WebSocket options
 * @returns {object} { marketState, error, connected }
 */
export function useMarketState(symbol, options = {}) {
    if (typeof React === 'undefined') {
        throw new Error('React is required for useMarketState hook');
    }
    
    const [marketState, setMarketState] = React.useState(null);
    const [error, setError] = React.useState(null);
    const [connected, setConnected] = React.useState(false);
    const wsRef = React.useRef(null);
    
    React.useEffect(() => {
        if (!symbol) return;
        
        wsRef.current = connectMarketState(
            symbol,
            (data) => {
                setMarketState(data);
                setError(null);
            },
            options
        );
        
        wsRef.current.on('open', () => setConnected(true));
        wsRef.current.on('close', () => setConnected(false));
        wsRef.current.on('error', (err) => setError(err));
        
        return () => {
            if (wsRef.current) {
                wsRef.current.close();
            }
        };
    }, [symbol]);
    
    return { marketState, error, connected };
}

// Export default for convenience
export default {
    connectTickStream,
    connectCandleStream,
    connectMarketState,
    useTickStream,
    useCandleStream,
    useMarketState,
    MarketDataWebSocket
};

