import { NavLink, useNavigate } from 'react-router-dom'
import { useTheme } from '../../hooks/useTheme'
import { useState, useRef, useEffect } from 'react'
import { supabase } from '../../supabase-client'
import AxBiLogo from '../ui/AxBiLogo'
import ProjectSelector from './ProjectSelector'

const navItems = [
  { to: '/agent',            icon: 'fa-solid fa-wand-magic-sparkles', label: 'AI Agent' },
  { to: '/BI-Dashboard',     icon: 'fa-solid fa-list-check',         label: 'Projects' },
  { to: '/upload',           icon: 'fa-solid fa-cloud-arrow-up',     label: 'Upload Data' },
  { to: '/AI-Insights',      icon: 'fa-solid fa-hexagon-nodes',      label: 'AI Insights' },
  { to: '/forecast-history', icon: 'fa-solid fa-clock-rotate-left',  label: 'Forecast History' },
  { to: '/recommendations',  icon: 'fa-solid fa-lightbulb',          label: 'Recommendations' },
  { to: '/report',           icon: 'fa-solid fa-passport',           label: 'Reports' },
  { to: '/voice-logs',       icon: 'fa-solid fa-clipboard-list',     label: 'Voice Logs' },
  { to: '/profile',          icon: 'fa-solid fa-user-pen',           label: 'Profile' },
]

function getInitials(name: string | null, email: string | null): string {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/).slice(0, 2)
    return parts.map(p => p[0]?.toUpperCase() || '').join('') || '?'
  }
  if (email) return email[0].toUpperCase()
  return '?'
}

export default function Sidebar() {
  const navigate = useNavigate()
  const { theme, toggle } = useTheme()
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [userEmail, setUserEmail] = useState<string | null>(null)
  const [userName, setUserName] = useState<string | null>(null)
  const [companyName, setCompanyName] = useState<string | null>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    supabase.auth.getUser().then(({ data }) => {
      const u = data.user
      setUserEmail(u?.email ?? null)
      const meta = (u?.user_metadata || {}) as Record<string, string>
      const name = (meta.name || meta.full_name || meta.display_name || '').toString().trim()
      setUserName(name || null)
      const co = (meta.company_name || meta.companyName || '').toString().trim()
      setCompanyName(co || null)
    })
  }, [])

  const firstName = userName?.split(/\s+/)[0] || ''
  const initials = getInitials(userName, userEmail)

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const handleSignOut = async () => {
    await supabase.auth.signOut()
    navigate('/login')
  }

  return (
    <>
      <nav className="fixed top-0 z-50 w-full bg-sidebar border-b border-border">
        <div className="px-3 py-3 lg:px-5 lg:pl-3">
          <div className="flex items-center justify-between">
            <div className="flex items-center justify-start rtl:justify-end">
              <button
                data-drawer-target="top-bar-sidebar"
                data-drawer-toggle="top-bar-sidebar"
                aria-controls="top-bar-sidebar"
                type="button"
                className="md:hidden text-heading bg-transparent box-border border border-transparent hover:bg-neutral-secondary-medium focus:ring-1 mr-1 focus:ring-neutral-tertiary font-medium leading-5 rounded-base text-sm p-2 focus:outline-none"
              >
                <span className="sr-only">Open sidebar</span>
                <svg className="w-6 h-6" aria-hidden="true" xmlns="http://www.w3.org/2000/svg" width="24" height="24" fill="none" viewBox="0 0 24 24">
                  <path stroke="currentColor" strokeLinecap="round" strokeWidth="2" d="M5 7h14M5 12h14M5 17h10" />
                </svg>
              </button>
              <h2 className="flex items-center font-bold text-xl caret-transparent text-foreground">
                <AxBiLogo className="h-7" />
              </h2>
              <div className="ml-3 hidden sm:block">
                <ProjectSelector />
              </div>
            </div>
            <div className="flex items-center gap-3">
              {/* Friendly greeting (hidden on small screens) */}
              {firstName && (
                <div className="hidden md:flex flex-col items-end leading-tight mr-1">
                  <span className="text-[10px] text-muted-foreground uppercase tracking-wider">Welcome</span>
                  <span className="text-sm text-foreground font-semibold truncate max-w-[160px]">{firstName}</span>
                </div>
              )}
              <button
                type="button"
                onClick={toggle}
                aria-label={theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode'}
                title={theme === 'dark' ? 'Light mode' : 'Dark mode'}
                className="flex items-center justify-center w-9 h-9 rounded-full text-muted-foreground hover:text-foreground hover:bg-accent focus:outline-none focus-visible:ring-2 focus-visible:ring-ring transition-colors"
              >
                <i className={theme === 'dark' ? 'fa-solid fa-sun' : 'fa-solid fa-moon'}></i>
              </button>
              <div className="relative flex items-center" ref={dropdownRef}>
                <button
                  type="button"
                  onClick={() => setDropdownOpen(prev => !prev)}
                  className="flex items-center justify-center w-9 h-9 rounded-full bg-gradient-to-br from-[#5A5AF6] to-[#7c3aed] text-primary-foreground text-sm font-bold shadow-md hover:shadow-[#5A5AF6]/40 hover:scale-105 transition-all focus:ring-2 focus:ring-primary/50 focus:outline-none"
                  aria-label={`User menu — ${userName || userEmail || ''}`}
                  title={userName || userEmail || 'User'}
                >
                  <span className="sr-only">Open user menu</span>
                  {initials}
                </button>
                {dropdownOpen && (
                  <div className="absolute right-0 top-full mt-2 z-50 bg-popover border border-border rounded-xl shadow-xl w-64 overflow-hidden">
                    <div className="px-4 py-3 border-b border-border bg-gradient-to-br from-[#5A5AF6]/10 to-[#7c3aed]/5">
                      <div className="flex items-center gap-3">
                        <div className="flex items-center justify-center w-10 h-10 rounded-full bg-gradient-to-br from-[#5A5AF6] to-[#7c3aed] text-primary-foreground text-sm font-bold flex-shrink-0">
                          {initials}
                        </div>
                        <div className="min-w-0 flex-1">
                          {userName && (
                            <p className="text-sm text-foreground font-semibold truncate">{userName}</p>
                          )}
                          {userEmail && (
                            <p className="text-xs text-muted-foreground truncate">{userEmail}</p>
                          )}
                          {companyName && (
                            <p className="text-[10px] text-muted-foreground truncate mt-0.5">
                              <i className="fa-solid fa-building text-[8px] mr-1" />
                              {companyName}
                            </p>
                          )}
                        </div>
                      </div>
                    </div>
                    <div className="p-2 space-y-0.5">
                      <button
                        onClick={() => {
                          setDropdownOpen(false)
                          navigate('/profile')
                        }}
                        className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-accent rounded-md transition-colors"
                      >
                        <i className="fa-solid fa-user-pen w-4 text-primary"></i>
                        Profile settings
                      </button>
                      <button
                        onClick={() => {
                          setDropdownOpen(false)
                          navigate('/voice-logs')
                        }}
                        className="flex items-center gap-2 w-full px-3 py-2 text-sm text-foreground hover:bg-accent rounded-md transition-colors"
                      >
                        <i className="fa-solid fa-clipboard-list w-4 text-info"></i>
                        Voice activity log
                      </button>
                      <div className="my-1 h-px bg-muted/50" />
                      <button
                        onClick={handleSignOut}
                        className="flex items-center gap-2 w-full px-3 py-2 text-sm text-destructive hover:bg-destructive/10 rounded-md transition-colors"
                      >
                        <i className="fa-solid fa-right-from-bracket w-4"></i>
                        Sign out
                      </button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          </div>
        </div>
      </nav>

      <aside id="top-bar-sidebar" className="fixed top-0 left-0 z-40 w-64 h-full transition-transform -translate-x-full md:translate-x-0" aria-label="Sidebar">
        <div className="h-full px-3 py-4 overflow-y-auto bg-sidebar border-e border-border">
          <a href="/" className="flex items-center ps-2.5 mb-5">
            <AxBiLogo className="h-7" />
          </a>
          <ul className="space-y-3 pt-3 font-medium text-foreground">
            {navItems.map(({ to, icon, label }) => (
              <li key={to}>
                <NavLink
                  to={to}
                  className={({ isActive }) =>
                    `flex items-center px-3 py-2 rounded-xl transition-colors duration-200 ${
                      isActive
                        ? 'bg-primary/15 text-primary font-semibold'
                        : 'hover:bg-accent hover:text-primary'
                    }`
                  }
                >
                  <i className={`${icon} w-4 text-center`}></i>
                  <span className="ms-3">{label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      </aside>
    </>
  )
}
