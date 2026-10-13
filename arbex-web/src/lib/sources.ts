// Source ids from the backend:
//   synthetic  bloomberg_tokyo_usdinr     -> provider "bloomberg", group "TOKYO"
//   live       upstox_nse_usdinr          -> provider "upstox",    group "NSE"
//   replay     replay_upstox_nse_usdinr   -> provider "upstox",    group "NSE", replay

export interface SourceParts {
  provider: string;
  group: string;      // session (synthetic) or exchange (live / replay)
  replay: boolean;
}

export function parseSourceId(sourceId: string): SourceParts {
  const replay = sourceId.startsWith('replay_');
  const parts = (replay ? sourceId.slice('replay_'.length) : sourceId).split('_');
  return {
    provider: parts[0] ?? sourceId,
    group: (parts[1] ?? '').toUpperCase(),
    replay,
  };
}

export function sourceLabel(sourceId: string): string {
  const { provider, group, replay } = parseSourceId(sourceId);
  return `${group} · ${provider}${replay ? ' (replay)' : ''}`;
}
