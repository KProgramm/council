/* All setup and queue requests use the same errors and timeout handling. */
async function requestJSON(path, payload, timeoutMs = 65000) {
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(HTTP_BASE + path, {
      method: payload === undefined ? 'GET' : 'POST', cache: 'no-store', signal: controller.signal,
      headers: payload === undefined ? {} : { 'Content-Type': 'application/json' },
      ...(payload === undefined ? {} : { body: JSON.stringify(payload) }),
    });
    let data;
    try { data = await response.json(); }
    catch { throw new Error('Council returned an unreadable response. Check that the server is running, then try again.'); }
    if (!response.ok) {
      const error = new Error(typeof data.detail === 'string' ? data.detail : 'Check the entered values and try again.');
      error.status = response.status;
      throw error;
    }
    return data;
  } catch (error) {
    if (error.name === 'AbortError') throw new Error('The request timed out. Check the connection, then try again.');
    if (error instanceof TypeError) throw new Error('Could not reach Council. Check that the local server is running.');
    throw error;
  } finally { clearTimeout(timeout); }
}
