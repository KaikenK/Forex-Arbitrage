// Source ids from the backend (one source per currency pair):
//   synthetic  bloomberg_tokyo_usdinr     -> provider "bloomberg", group "TOKYO", symbol "USDINR"
//   live       upstox_nse_eurinr          -> provider "upstox",    group "NSE",   symbol "EURINR"
//   replay     replay_upstox_nse_usdinr   -> provider "upstox",    group "NSE",   replay

export interface SourceParts {
  provider: string;
  group: string;      // session (synthetic) or exchange (live / replay)
  symbol: string;     // currency pair, from the id suffix
  replay: boolean;
}

export function parseSourceId(sourceId: string): SourceParts {
  const replay = sourceId.startsWith('replay_');
  const parts = (replay ? sourceId.slice('replay_'.length) : sourceId).split('_');
  const tail = (parts[parts.length - 1] ?? '').toUpperCase();
  return {
    provider: parts[0] ?? sourceId,
    group: (parts[1] ?? '').toUpperCase(),
    symbol: /^[A-Z]{6}$/.test(tail) ? tail : 'USDINR',
    replay,
  };
}

export function sourceSymbol(sourceId: string): string {
  return parseSourceId(sourceId).symbol;
}

export function sourceLabel(sourceId: string): string {
  const { provider, group, replay } = parseSourceId(sourceId);
  return `${group} · ${provider}${replay ? ' (replay)' : ''}`;
}
