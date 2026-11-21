export function canUseVisionForFile(filename: string): boolean {
  return /\.(pdf|png|jpe?g)$/i.test(filename)
}

export function canUseVisionForMime(mime: string | null): boolean {
  return mime === 'application/pdf' || mime === 'image/png' || mime === 'image/jpeg'
}
