export {};

declare global {
  const chrome: {
    runtime: {
      onInstalled: {
        addListener(callback: () => void | Promise<void>): void;
      };
      onMessage: {
        addListener(
          callback: (
            message: any,
            sender: any,
            sendResponse: (response?: any) => void,
          ) => boolean | void,
        ): void;
      };
      openOptionsPage(): void | Promise<void>;
    };
    storage: {
      local: {
        get<T extends Record<string, unknown>>(defaults?: T): Promise<T & Record<string, unknown>>;
        set(values: Record<string, unknown>): Promise<void>;
      };
    };
    tabs?: {
      query(queryInfo: { active: boolean; currentWindow: boolean }): Promise<
        Array<{ id?: number; title?: string; url?: string }>
      >;
      sendMessage(tabId: number, message: Record<string, unknown>): Promise<any>;
    };
  };
}
