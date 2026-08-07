import type { InfiniteData } from '@tanstack/react-query'

// Offset-based "Load more" pagination computes each page's offset from
// already-fetched pages, not a stable cursor — a concurrent insert/delete
// on the same list between two fetches can shift the underlying ordering
// enough that a row already rendered on an earlier page gets fetched again
// as part of the next one. Deduping by id when pages are read keeps that
// from surfacing as a duplicate row (and a duplicate React key), without
// requiring cursor-based pagination on the backend. The rarer mirror case
// (a row skipped entirely) isn't fixed by this and self-heals on refresh.
export function dedupeInfinitePages<TPage extends { items: { id: string }[] }>(
  data: InfiniteData<TPage>,
): InfiniteData<TPage> {
  const seen = new Set<string>()
  return {
    ...data,
    pages: data.pages.map((page) => ({
      ...page,
      items: page.items.filter((item) => {
        if (seen.has(item.id)) return false
        seen.add(item.id)
        return true
      }),
    })),
  }
}
