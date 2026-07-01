import { useState, useRef, useCallback, useEffect } from 'react'
import { Outlet, useLocation } from 'react-router-dom'
import Sidebar from '../Sidebar/Sidebar'
import ChatPanel from '../Chat/ChatPanel'
import CanvasPanel from '../Chat/CanvasPanel'
import { GeneratedAssetsProvider } from '../../context/GeneratedAssetsContext'

function DraggableChatButton({ onClick, hidden }: { onClick: () => void; hidden: boolean }) {
  const btnRef = useRef<HTMLButtonElement>(null)
  const [pos, setPos] = useState(() => {
    const saved = localStorage.getItem('chat-btn-pos')
    if (saved) {
      try { return JSON.parse(saved) } catch { /* ignore */ }
    }
    return { x: window.innerWidth - 72, y: window.innerHeight - 72 }
  })
  const dragging = useRef(false)
  const offset = useRef({ x: 0, y: 0 })
  const moved = useRef(false)

  const clamp = useCallback((x: number, y: number) => {
    const size = 48
    return {
      x: Math.max(8, Math.min(window.innerWidth - size - 8, x)),
      y: Math.max(8, Math.min(window.innerHeight - size - 8, y)),
    }
  }, [])

  useEffect(() => {
    const handleResize = () => {
      setPos((p: { x: number; y: number }) => clamp(p.x, p.y))
    }
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [clamp])

  const onPointerDown = useCallback((e: React.PointerEvent) => {
    dragging.current = true
    moved.current = false
    offset.current = { x: e.clientX - pos.x, y: e.clientY - pos.y }
    btnRef.current?.setPointerCapture(e.pointerId)
  }, [pos])

  const onPointerMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current) return
    moved.current = true
    const newPos = clamp(e.clientX - offset.current.x, e.clientY - offset.current.y)
    setPos(newPos)
  }, [clamp])

  const onPointerUp = useCallback((e: React.PointerEvent) => {
    dragging.current = false
    btnRef.current?.releasePointerCapture(e.pointerId)
    localStorage.setItem('chat-btn-pos', JSON.stringify(pos))
    if (!moved.current) {
      onClick()
    }
  }, [onClick, pos])

  if (hidden) return null

  return (
    <button
      ref={btnRef}
      onPointerDown={onPointerDown}
      onPointerMove={onPointerMove}
      onPointerUp={onPointerUp}
      className="fixed z-40 w-12 h-12 rounded-full bg-gradient-to-br from-[#5A5AF6] to-[#8B5CF6] shadow-lg shadow-primary/25 flex items-center justify-center hover:scale-110 active:scale-95 transition-transform cursor-grab active:cursor-grabbing select-none touch-none"
      style={{ left: pos.x, top: pos.y }}
      title="Open AxBi Assistant (drag to move)"
    >
      <i className="fa-solid fa-comment-dots text-foreground text-lg pointer-events-none" />
    </button>
  )
}

export default function Layout() {
  const [chatOpen, setChatOpen] = useState(false)
  const location = useLocation()
  const isConversationPage = location.pathname.startsWith('/conversation/')

  return (
    <GeneratedAssetsProvider>
      <div className='bg-background text-foreground min-h-screen'>
        <Sidebar />
        <div className='md:ml-64 pt-14'>
          <Outlet />
        </div>

        {/* Draggable chat toggle button */}
        <DraggableChatButton
          onClick={() => setChatOpen(true)}
          hidden={chatOpen || isConversationPage}
        />

        {/* Chat panel */}
        <ChatPanel open={chatOpen} onClose={() => setChatOpen(false)} />

        {/* Canvas: gallery of AI-generated assets (voice + chat) */}
        <CanvasPanel />

        {/* Backdrop for mobile */}
        {chatOpen && (
          <div
            className="fixed inset-0 bg-black/40 z-40 md:hidden"
            onClick={() => setChatOpen(false)}
          />
        )}
      </div>
    </GeneratedAssetsProvider>
  )
}
