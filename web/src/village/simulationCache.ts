type Inputs = {
  villageId: string; mode: 'live' | 'replay'; day: number;
  intervention: { type: string; targets: string[]; delta_hc_cm: number; days: number };
  inputVersion?: string; historyVersion: number;
};

export function simulationKey(inputs: Inputs): string | null {
  // Missing input provenance cannot establish that future weather is unchanged.
  if (!inputs.inputVersion) return null;
  return JSON.stringify({ ...inputs, day: inputs.mode === 'live' ? 0 : inputs.day,
    historyVersion: inputs.mode === 'replay' ? 0 : inputs.historyVersion });
}

export function createSimulationCache<T>(limit = 32) {
  const entries = new Map<string, Promise<T>>();
  return {
    get(key: string | null, load: () => Promise<T>): Promise<T> {
      if (key === null) return load();
      const existing = entries.get(key);
      if (existing) {
        entries.delete(key); entries.set(key, existing);
        return existing;
      }
      // A request belongs to the cache, not a mounted consumer's abort signal.
      const request = Promise.resolve().then(load).catch(error => {
        if (entries.get(key) === request) entries.delete(key);
        throw error;
      });
      entries.set(key, request);
      while (entries.size > limit) entries.delete(entries.keys().next().value!);
      return request;
    },
  };
}
