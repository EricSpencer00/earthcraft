/** Admit in-memory private archive work only with room for durable metadata. */
export const MIN_LOCAL_FREE_BYTES = 512 * 2 ** 20;

export function localCapacityWait(freeBytes, memoryUpload) {
  if (!Number.isSafeInteger(freeBytes) || freeBytes < 0) throw Error('Invalid local capacity');
  if (memoryUpload !== true || freeBytes >= MIN_LOCAL_FREE_BYTES) return null;
  return {available_bytes: freeBytes, required_bytes: MIN_LOCAL_FREE_BYTES};
}
