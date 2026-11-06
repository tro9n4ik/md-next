export type NodeTransport = 'xray' | 'mdnt';

export const transportLabel = (transport?: NodeTransport) => transport === 'mdnt' ? 'KedrLink — Go-агент' : 'Стандартный';
