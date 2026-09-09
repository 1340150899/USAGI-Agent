// Transport logs intentionally omit message bodies, user IDs, tokens and URLs.
const noop = (..._args: unknown[]) => {};
export const logger = {debug:noop,info:noop,warn:noop,error:noop};
