# References — FX Arbitrage Detection Research System

## System Positioning

> This system is a **pre-trade arbitrage intelligence platform**.
> Execution is simulated and used for feasibility analysis only.
> No real capital is at risk. All trading signals are advisory.

---

## Academic References

### Core Methodology

1. **Hull, J.C. (2018)**. *Options, Futures, and Other Derivatives*, 10th ed., Pearson.
   - Ch.5: No-Arbitrage Arguments — foundation for the cross-source arbitrage condition (`bid_A > ask_B`).

2. **Chaboud, A., Chiquoine, B., Hjalmarsson, E. & Vega, C. (2014)**. "Rise of the Machines: Algorithmic Trading in the Foreign Exchange Market." *Journal of Finance*, 69(5), pp.2045–2084.
   - Establishes that HFT triangular and cross-source arbitrage opportunities exist but are short-lived (sub-second). Motivates our persistence classification system.

3. **Aiba, Y., Hatano, N., Takayasu, H., Marumo, K. & Shimizu, T. (2002)**. "Triangular Arbitrage as an Interaction Among Foreign Exchange Rates." *Physica A*, 310(3-4), pp.467–479.
   - Mathematical formulation of triangular arbitrage as rate product deviation from unity. Directly implemented in our `ArbitrageEngine.detect_triangular()`.

### Market Microstructure

4. **Foucault, T., Pagano, M. & Röell, A. (2013)**. *Market Liquidity: Theory, Evidence, and Policy*. Oxford University Press.
   - Ch.4: Bid-ask spread dynamics and price discovery across venues. Theoretical basis for our cross-source spread analysis.

5. **Hasbrouck, J. & Saar, G. (2013)**. "Low-Latency Trading." *Journal of Financial Markets*, 16(4), pp.646–679.
   - Latency thresholds for signal classification. Basis for our ephemeral (<50ms) / flickering (50–300ms) / persistent (>300ms) taxonomy.

### Execution & Transaction Costs

6. **Gatheral, J. (2010)**. "No-Dynamic-Arbitrage and Market Impact." *Quantitative Finance*, 10(7), pp.749–759.
   - Market impact modelling. Informs our `SimulatedExecutionFilter` slippage estimation.

7. **Almgren, R. & Chriss, N. (2001)**. "Optimal Execution of Portfolio Transactions." *Journal of Risk*, 3(2), pp.5–39.
   - Optimal execution theory. Theoretical foundation for why execution costs erode arbitrage profits.

### Session & Liquidity Effects

8. **Breedon, F. & Ranaldo, A. (2013)**. "Intraday Patterns in FX Returns and Order Flow." *Journal of Money, Credit and Banking*, 45(5), pp.953–965.
   - Intraday FX liquidity patterns across Tokyo/London/New York sessions. Basis for our session-weighted opportunity scoring.

### Statistical Arbitrage (Extended Context)

9. **Avellaneda, M. & Lee, J.-H. (2010)**. "Statistical Arbitrage in the US Equities Market." *Quantitative Finance*, 10(7), pp.761–782.
   - While focused on equities, provides the statistical framework for persistence-based arbitrage detection that informs our approach.

---

## Tools & Technologies

- **MetaTrader 5 (MT5)**: Retail FX data source via `MetaTrader5` Python package
- **FastAPI**: Async web framework for REST and WebSocket endpoints
- **Python 3.12+**: Core language with `asyncio` for concurrent streaming
- **WebSocket**: Real-time data distribution to frontend dashboards
- **Lightweight Charts v4.1.3**: TradingView-grade candlestick chart rendering
- **Motion One**: Framer Motion-compatible animation library for vanilla JS
- **Inter / JetBrains Mono**: Typography (Google Fonts) for UI labels and data display
