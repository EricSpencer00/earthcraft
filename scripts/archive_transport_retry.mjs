/** Retry only transient transport failures; never restart generation or authentication. */
export function isTransientTransport(error) {
  if (/permission denied|authentication|host key|identity file|configuration|bad owner/i.test(error.message)) return false;
  return (error.executable === '/usr/bin/ssh' && (error.code === 255 || error.timedOut === true)) ||
    (error.executable === '/usr/bin/rsync' && ([10, 12, 30, 35, 255].includes(error.code) || error.timedOut === true));
}

export async function retryTransport(work, {sleep = ms => new Promise(r => setTimeout(r, ms)),
  attempts = 4, onRetry = async () => {}, shouldStop = () => false} = {}) {
  for (let attempt = 1; ; attempt++) {
    try { return await work(); }
    catch (error) {
      if (!isTransientTransport(error) || attempt >= attempts || shouldStop()) throw error;
      const delay = Math.min(30000, 5000 * 2 ** (attempt - 1));
      await onRetry({attempt, delay, reason: error.message});
      await sleep(delay);
      if (shouldStop()) throw error;
    }
  }
}
