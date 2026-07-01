import { useState, lazy, Suspense } from 'react'
import ChatChartRenderer from '../Conversation/ChatChart'
import { useGeneratedAssets, type GeneratedAsset } from '../../context/GeneratedAssetsContext'

const Visual3D = lazy(() => import('../Conversation/Visual3D'))

function AssetBody({ asset, height }: { asset: GeneratedAsset; height: number }) {
  if (asset.kind === 'chart' && asset.chart) {
    return <ChatChartRenderer chart={asset.chart} height={height} />
  }
  if (asset.kind === '3d' && asset.visual3d) {
    return (
      <Suspense fallback={<div className="h-full flex items-center justify-center text-muted-foreground text-xs">Loading 3D…</div>}>
        <Visual3D visual={asset.visual3d} height={height} />
      </Suspense>
    )
  }
  return null
}

export default function CanvasPanel() {
  const { assets, canvasOpen, closeCanvas, removeAsset, clearAssets, pinAsset, unpinAsset, isPinned } = useGeneratedAssets()
  const [expanded, setExpanded] = useState<GeneratedAsset | null>(null)

  const ordered = [...assets].reverse() // newest first

  return (
    <>
      {/* Backdrop (above the conversation overlay so it works from convo mode too) */}
      <div
        className={`fixed inset-0 z-[135] bg-black/50 transition-opacity duration-300 ${
          canvasOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={closeCanvas}
      />

      {/* Slide-in panel */}
      <div
        className={`fixed top-0 right-0 h-full z-[140] flex flex-col bg-background border-l border-border shadow-2xl transition-transform duration-300 ease-in-out ${
          canvasOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
        style={{ width: 480, maxWidth: '100vw' }}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-4 py-3 border-b border-border shrink-0">
          <div className="flex items-center gap-2">
            <span className="w-8 h-8 rounded-lg bg-gradient-to-br from-[#5A5AF6] to-[#a855f7] flex items-center justify-center text-sm">✨</span>
            <div>
              <h3 className="text-sm font-semibold text-foreground leading-tight">Canvas</h3>
              <p className="text-[10px] text-muted-foreground leading-tight">{assets.length} AI-generated {assets.length === 1 ? 'asset' : 'assets'}</p>
            </div>
          </div>
          <div className="flex items-center gap-1">
            {assets.length > 0 && (
              <button
                onClick={clearAssets}
                className="text-[11px] text-muted-foreground hover:text-destructive px-2 py-1 rounded-md hover:bg-card transition-colors"
              >
                Clear all
              </button>
            )}
            <button
              onClick={closeCanvas}
              className="w-8 h-8 rounded-lg hover:bg-card flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors"
              title="Close"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
            </button>
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {ordered.length === 0 && (
            <div className="h-full flex flex-col items-center justify-center text-center px-6 py-16">
              <div className="w-14 h-14 rounded-2xl bg-card border border-border flex items-center justify-center text-2xl mb-3">🎨</div>
              <p className="text-sm text-muted-foreground font-medium mb-1">No assets yet</p>
              <p className="text-xs text-muted-foreground max-w-[260px]">
                Charts and 3D visuals you create with the AI assistant (typed or by voice) collect here.
              </p>
            </div>
          )}

          {ordered.map((asset) => {
            const pinned = isPinned(asset.id)
            return (
              <div key={asset.id} className="rounded-xl border border-border bg-card overflow-hidden">
                <div className="flex items-center justify-between gap-2 px-3 py-2 border-b border-border">
                  <span className="text-xs font-semibold text-foreground truncate flex items-center gap-1.5">
                    <span>{asset.kind === '3d' ? '🧊' : '📊'}</span>
                    {asset.title}
                  </span>
                  <span className="text-[9px] text-muted-foreground shrink-0 flex items-center gap-1">
                    <span className={`w-1.5 h-1.5 rounded-full ${asset.source === 'voice' ? 'bg-[#7c3aed]' : 'bg-emerald-400'}`} />
                    {asset.source === 'voice' ? 'voice' : 'chat'}
                  </span>
                </div>
                <div className="p-2">
                  <AssetBody asset={asset} height={220} />
                </div>
                <div className="flex items-center gap-1.5 px-3 py-2 border-t border-border">
                  <button
                    onClick={() => setExpanded(asset)}
                    className="text-[11px] text-muted-foreground hover:text-foreground px-2 py-1 rounded-md hover:bg-card transition-colors flex items-center gap-1"
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M15 3h6v6M9 21H3v-6M21 3l-7 7M3 21l7-7"/></svg>
                    Expand
                  </button>
                  <button
                    onClick={() => (pinned ? unpinAsset(asset.id) : pinAsset(asset))}
                    className={`text-[11px] px-2 py-1 rounded-md transition-colors flex items-center gap-1 ${
                      pinned
                        ? 'text-warning bg-amber-500/10 hover:bg-amber-500/20'
                        : 'text-muted-foreground hover:text-foreground hover:bg-card'
                    }`}
                    title={pinned ? 'Pinned to dashboard' : 'Pin to dashboard'}
                  >
                    <svg width="12" height="12" viewBox="0 0 24 24" fill={pinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M12 17v5M9 10.76a2 2 0 0 1-1.11 1.79l-1.78.9A2 2 0 0 0 5 15.24V16a1 1 0 0 0 1 1h12a1 1 0 0 0 1-1v-.76a2 2 0 0 0-1.11-1.79l-1.78-.9A2 2 0 0 1 15 10.76V7a1 1 0 0 1 1-1 2 2 0 0 0 0-4H8a2 2 0 0 0 0 4 1 1 0 0 1 1 1z"/></svg>
                    {pinned ? 'Pinned' : 'Pin to dashboard'}
                  </button>
                  <button
                    onClick={() => removeAsset(asset.id)}
                    className="ml-auto text-[11px] text-muted-foreground hover:text-destructive px-2 py-1 rounded-md hover:bg-red-500/10 transition-colors"
                  >
                    Remove
                  </button>
                </div>
              </div>
            )
          })}
        </div>
      </div>

      {/* Expand modal */}
      {expanded && (
        <div className="fixed inset-0 z-[150] flex items-center justify-center bg-black/75 backdrop-blur-sm px-4" onClick={() => setExpanded(null)}>
          <div
            className="relative w-[92vw] max-w-4xl h-[72vh] bg-background rounded-2xl border border-border shadow-2xl p-4 flex flex-col"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-foreground flex items-center gap-2">
                <span>{expanded.kind === '3d' ? '🧊' : '📊'}</span>{expanded.title}
              </h3>
              <button onClick={() => setExpanded(null)} className="w-8 h-8 rounded-lg hover:bg-card flex items-center justify-center text-muted-foreground hover:text-foreground transition-colors">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round"><path d="M18 6 6 18M6 6l12 12"/></svg>
              </button>
            </div>
            <div className="flex-1 rounded-xl overflow-hidden">
              <AssetBody asset={expanded} height={460} />
            </div>
          </div>
        </div>
      )}
    </>
  )
}
