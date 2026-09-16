import { SkeletonLine } from "./primitives";
import { PanelHeaderSkeleton } from "./PanelHeaderSkeleton";

export function PersonaPlazaSkeleton() {
  return (
    <div className="flex h-full min-h-0 flex-col gap-4 animate-fade-in">
      <PanelHeaderSkeleton hasSearch />
      <div className="skill-content-area flex-1 overflow-y-auto py-2 sm:py-4 px-4 sm:p-6 lg:px-8 lg:py-8">
        <div className="grid auto-grid-cols gap-4 sm:gap-5">
          {Array.from({ length: 12 }).map((_, i) => (
            <div key={i} className="scb">
              {/* Card body */}
              <div className="flex flex-1 flex-col pt-5 p-5">
                <div className="flex items-start gap-3">
                  <div className="scb__icon-ring shrink-0 skeleton-line" />
                  <div className="min-w-0 flex-1">
                    <SkeletonLine
                      width={i % 2 === 0 ? "w-3/4" : "w-1/2"}
                      className="!h-4"
                    />
                    {/* Metadata line — scope, status, usage count */}
                    <SkeletonLine
                      width="w-3/5"
                      className="!h-2.5 mt-1 !opacity-50"
                    />
                  </div>
                </div>
                <div className="mt-3 space-y-1.5">
                  <SkeletonLine width="w-full" className="!h-3" />
                  <SkeletonLine
                    width={i % 2 === 0 ? "w-5/6" : "w-2/3"}
                    className="!h-3"
                  />
                </div>
                {/* Tags */}
                <div className="mt-3 flex flex-wrap gap-1.5">
                  <SkeletonLine width="w-14" className="!h-5 !rounded-full" />
                  <SkeletonLine width="w-10" className="!h-5 !rounded-full" />
                  <SkeletonLine width="w-16" className="!h-5 !rounded-full" />
                </div>
                {/* Footer — skill count on left, action buttons on right */}
                <div
                  className="mt-4 flex items-center justify-between border-t pt-3"
                  style={{ borderColor: "var(--theme-border)" }}
                >
                  <SkeletonLine width="w-12" className="!h-3 !opacity-50" />
                  <div className="flex gap-1.5">
                    <SkeletonLine width="w-12" className="!h-7 !rounded-lg" />
                    <SkeletonLine width="w-12" className="!h-7 !rounded-lg" />
                    <SkeletonLine width="w-12" className="!h-7 !rounded-lg" />
                    <SkeletonLine width="w-12" className="!h-7 !rounded-lg" />
                  </div>
                </div>
                <div className="flex-1" />
              </div>
            </div>
          ))}
        </div>
        {/* Pagination placeholder */}
        <div className="glass-divider px-3 py-3 sm:px-6 mt-2">
          <div className="flex items-center justify-center gap-2">
            <div className="skeleton-line size-8 rounded-lg" />
            <div className="skeleton-line w-24 h-3" />
            <div className="skeleton-line size-8 rounded-lg" />
          </div>
        </div>
      </div>
    </div>
  );
}

export function PersonaPageSkeleton() {
  return (
    <div className="flex h-full animate-fade-in">
      <PersonaPlazaSkeleton />
    </div>
  );
}
