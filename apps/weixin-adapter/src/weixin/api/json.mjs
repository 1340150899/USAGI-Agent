/** Parse Weixin JSON without rounding message identifiers beyond Number.MAX_SAFE_INTEGER. */
export function parseWeixinJson(rawText) {
  const safeText = rawText.replace(
    /("(?:message_id|msg_id)"\s*:\s*)(-?\d+)/g,
    '$1"$2"',
  );
  return JSON.parse(safeText);
}
