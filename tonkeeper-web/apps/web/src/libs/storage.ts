import { IStorage } from '@tonkeeper/core/dist/Storage';

// Embedded browsers can deny localStorage access. Keep the wallet usable for
// the current session instead of letting a storage exception crash React.
const volatileStorage = new Map<string, string>();

function readItem(key: string): string | null {
    try {
        const value = window.localStorage.getItem(key);
        if (value !== null) volatileStorage.set(key, value);
        return value ?? volatileStorage.get(key) ?? null;
    } catch {
        return volatileStorage.get(key) ?? null;
    }
}

function writeItem(key: string, value: string): void {
    volatileStorage.set(key, value);
    try { window.localStorage.setItem(key, value); } catch { /* Persistent storage may be disabled in a WebView. */ }
}

function removeItem(key: string): void {
    volatileStorage.delete(key);
    try { window.localStorage.removeItem(key); } catch { /* Persistent storage may be disabled in a WebView. */ }
}

export class BrowserStorage implements IStorage {
    prefix = 'tonkeeper';

    get = async <R>(key: string) => {
        const storageKey = `${this.prefix}_${key}`;
        const value = readItem(storageKey);
        if (!value) return null;
        try {
            const { payload } = JSON.parse(value) as { payload: R };
            return payload;
        } catch (error) {
            // A stale or partially-written preference must not make the entire
            // wallet fail during its suspense-based startup queries.
            console.warn(`Ignoring malformed wallet storage entry: ${key}`, error);
            removeItem(storageKey);
            return null;
        }
    };

    set = async <R>(key: string, payload: R) => {
        writeItem(`${this.prefix}_${key}`, JSON.stringify({ payload }));
        return payload;
    };

    setBatch = async <V extends Record<string, unknown>>(values: V) => {
        Object.entries(values).forEach(([key, payload]) => {
            writeItem(`${this.prefix}_${key}`, JSON.stringify({ payload }));
        });
        return values;
    };

    delete = async <R>(key: string) => {
        const payload = await this.get<R>(key);
        if (payload != null) {
            removeItem(`${this.prefix}_${key}`);
        }
        return payload;
    };

    clear = async () => {
        for (const key of volatileStorage.keys()) {
            if (key.startsWith(`${this.prefix}_`)) removeItem(key);
        }
        try {
            for (let index = window.localStorage.length - 1; index >= 0; index -= 1) {
                const key = window.localStorage.key(index);
                if (key?.startsWith(`${this.prefix}_`)) removeItem(key);
            }
        } catch { /* Persistent storage may be disabled in a WebView. */ }
    };
}
