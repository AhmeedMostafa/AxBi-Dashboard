import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import toast from 'react-hot-toast'
import { supabase } from '../../supabase-client'
import { LogoSpinner } from '../ui/LogoSpinner'

interface ProfileForm {
  name: string
  companyName: string
  industrialField: string
}

interface EmailForm {
  email: string
}

interface PasswordForm {
  newPassword: string
  confirmPassword: string
}

function getInitials(name: string, email: string): string {
  if (name && name.trim()) {
    const parts = name.trim().split(/\s+/).slice(0, 2)
    return parts.map(p => p[0]?.toUpperCase() || '').join('') || '?'
  }
  if (email) return email[0].toUpperCase()
  return '?'
}

export default function ProfilePage() {
  const navigate = useNavigate()
  const [loading, setLoading] = useState(true)
  const [savingProfile, setSavingProfile] = useState(false)
  const [savingEmail, setSavingEmail] = useState(false)
  const [savingPassword, setSavingPassword] = useState(false)
  const [signingOut, setSigningOut] = useState(false)

  const [profile, setProfile] = useState<ProfileForm>({
    name: '',
    companyName: '',
    industrialField: '',
  })
  const [originalProfile, setOriginalProfile] = useState<ProfileForm>(profile)
  const [emailForm, setEmailForm] = useState<EmailForm>({ email: '' })
  const [originalEmail, setOriginalEmail] = useState('')
  const [passwordForm, setPasswordForm] = useState<PasswordForm>({
    newPassword: '',
    confirmPassword: '',
  })
  const [memberSince, setMemberSince] = useState<string>('')
  const [provider, setProvider] = useState<string>('email')

  useEffect(() => {
    let mounted = true
    supabase.auth.getUser().then(({ data, error }) => {
      if (!mounted) return
      if (error || !data.user) {
        toast.error('Could not load profile')
        navigate('/login')
        return
      }
      const u = data.user
      const meta = (u.user_metadata || {}) as Record<string, string>
      const next: ProfileForm = {
        name: (meta.name || meta.full_name || meta.display_name || '').toString(),
        companyName: (meta.company_name || meta.companyName || '').toString(),
        industrialField: (meta.industrial_field || meta.industrialField || '').toString(),
      }
      setProfile(next)
      setOriginalProfile(next)
      setEmailForm({ email: u.email || '' })
      setOriginalEmail(u.email || '')
      if (u.created_at) {
        try {
          setMemberSince(new Date(u.created_at).toLocaleDateString(undefined, {
            year: 'numeric', month: 'long', day: 'numeric',
          }))
        } catch { /* ignore */ }
      }
      const app = (u.app_metadata || {}) as Record<string, any>
      setProvider(app.provider || 'email')
      setLoading(false)
    })
    return () => { mounted = false }
  }, [navigate])

  const profileDirty = (
    profile.name !== originalProfile.name ||
    profile.companyName !== originalProfile.companyName ||
    profile.industrialField !== originalProfile.industrialField
  )
  const emailDirty = emailForm.email.trim() !== originalEmail.trim()
  const passwordReady = passwordForm.newPassword.length >= 6 && passwordForm.newPassword === passwordForm.confirmPassword

  const initials = getInitials(profile.name, emailForm.email)

  const handleProfileSave = async () => {
    if (!profileDirty) return
    setSavingProfile(true)
    try {
      const { error } = await supabase.auth.updateUser({
        data: {
          name: profile.name.trim(),
          company_name: profile.companyName.trim(),
          industrial_field: profile.industrialField.trim(),
        },
      })
      if (error) throw error
      setOriginalProfile(profile)
      toast.success('Profile updated')
    } catch (err: any) {
      toast.error(err?.message || 'Failed to update profile')
    } finally {
      setSavingProfile(false)
    }
  }

  const handleEmailSave = async () => {
    if (!emailDirty) return
    const newEmail = emailForm.email.trim()
    if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(newEmail)) {
      toast.error('Please enter a valid email address')
      return
    }
    setSavingEmail(true)
    try {
      const { error } = await supabase.auth.updateUser({ email: newEmail })
      if (error) throw error
      toast.success('Verification email sent. Check your inbox to confirm the change.')
      setOriginalEmail(newEmail)
    } catch (err: any) {
      toast.error(err?.message || 'Failed to change email')
    } finally {
      setSavingEmail(false)
    }
  }

  const handlePasswordSave = async () => {
    if (!passwordReady) return
    setSavingPassword(true)
    try {
      const { error } = await supabase.auth.updateUser({ password: passwordForm.newPassword })
      if (error) throw error
      setPasswordForm({ newPassword: '', confirmPassword: '' })
      toast.success('Password updated')
    } catch (err: any) {
      toast.error(err?.message || 'Failed to update password')
    } finally {
      setSavingPassword(false)
    }
  }

  const handleSignOut = async () => {
    setSigningOut(true)
    await supabase.auth.signOut()
    navigate('/login')
  }

  const handleResetProfile = () => setProfile(originalProfile)

  if (loading) {
    return (
      <div className="min-h-[60vh] flex items-center justify-center gap-2 text-muted-foreground text-sm">
        <LogoSpinner size={18} />
        Loading profile…
      </div>
    )
  }

  return (
    <div className="max-w-3xl mx-auto px-4 py-8 space-y-6">
      {/* Header */}
      <div className="flex items-center gap-5">
        <div className="w-20 h-20 rounded-2xl bg-gradient-to-br from-[#5A5AF6] to-[#7c3aed] flex items-center justify-center text-primary-foreground text-2xl font-bold shadow-lg shadow-[#5A5AF6]/30 flex-shrink-0">
          {initials}
        </div>
        <div className="min-w-0 flex-1">
          <h1 className="text-2xl font-bold text-foreground truncate">
            {profile.name || 'Your Profile'}
          </h1>
          <p className="text-sm text-muted-foreground truncate">{emailForm.email}</p>
          <div className="mt-2 flex flex-wrap items-center gap-2 text-[11px]">
            {memberSince && (
              <span className="px-2 py-0.5 rounded-md bg-card border border-border text-muted-foreground">
                <i className="fa-solid fa-calendar-days text-[9px] mr-1" />
                Member since {memberSince}
              </span>
            )}
            <span className="px-2 py-0.5 rounded-md bg-card border border-border text-muted-foreground capitalize">
              <i className="fa-solid fa-shield-halved text-[9px] mr-1" />
              {provider} sign-in
            </span>
          </div>
        </div>
      </div>

      {/* Personal info card */}
      <section className="bg-card border border-border rounded-2xl overflow-hidden">
        <header className="px-6 py-4 border-b border-border flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-primary/15 border border-primary/30 flex items-center justify-center text-primary">
            <i className="fa-solid fa-user text-xs" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-foreground">Personal info</h2>
            <p className="text-[11px] text-muted-foreground">How AxBi addresses you in dashboards, reports, and voice overviews.</p>
          </div>
        </header>
        <div className="px-6 py-5 space-y-4">
          <Field
            label="Full name"
            value={profile.name}
            placeholder="e.g. Omar Khaled"
            onChange={(v) => setProfile(p => ({ ...p, name: v }))}
            hint="Used in personalized greetings on audio overviews and the chatbot."
          />
          <Field
            label="Company"
            value={profile.companyName}
            placeholder="e.g. Acme Analytics"
            onChange={(v) => setProfile(p => ({ ...p, companyName: v }))}
          />
          <Field
            label="Industrial field"
            value={profile.industrialField}
            placeholder="e.g. Retail · Finance · Healthcare"
            onChange={(v) => setProfile(p => ({ ...p, industrialField: v }))}
          />
        </div>
        <footer className="px-6 py-3 bg-muted border-t border-border flex items-center justify-end gap-2">
          {profileDirty && (
            <button
              onClick={handleResetProfile}
              disabled={savingProfile}
              className="px-3 py-2 text-xs font-medium text-muted-foreground hover:text-foreground transition-colors"
            >
              Reset
            </button>
          )}
          <button
            onClick={handleProfileSave}
            disabled={!profileDirty || savingProfile}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
              !profileDirty
                ? 'bg-card border border-border text-muted-foreground cursor-not-allowed'
                : 'bg-gradient-to-r from-[#5A5AF6] to-[#7c3aed] text-primary-foreground hover:shadow-lg hover:shadow-[#5A5AF6]/30'
            }`}
          >
            {savingProfile ? (<><LogoSpinner size={14} className="mr-1.5 inline-block align-middle" /> Saving…</>) : 'Save changes'}
          </button>
        </footer>
      </section>

      {/* Email card */}
      <section className="bg-card border border-border rounded-2xl overflow-hidden">
        <header className="px-6 py-4 border-b border-border flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/15 border border-emerald-500/30 flex items-center justify-center text-success">
            <i className="fa-solid fa-envelope text-xs" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-foreground">Email address</h2>
            <p className="text-[11px] text-muted-foreground">A verification link will be sent to the new email before the change is applied.</p>
          </div>
        </header>
        <div className="px-6 py-5">
          <Field
            label="Email"
            value={emailForm.email}
            placeholder="you@company.com"
            onChange={(v) => setEmailForm({ email: v })}
            type="email"
          />
        </div>
        <footer className="px-6 py-3 bg-muted border-t border-border flex items-center justify-end gap-2">
          <button
            onClick={handleEmailSave}
            disabled={!emailDirty || savingEmail}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
              !emailDirty
                ? 'bg-card border border-border text-muted-foreground cursor-not-allowed'
                : 'bg-primary text-primary-foreground hover:bg-primary/90'
            }`}
          >
            {savingEmail ? (<><LogoSpinner size={14} className="mr-1.5 inline-block align-middle" /> Sending…</>) : 'Update email'}
          </button>
        </footer>
      </section>

      {/* Password card */}
      <section className="bg-card border border-border rounded-2xl overflow-hidden">
        <header className="px-6 py-4 border-b border-border flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-amber-500/15 border border-amber-500/30 flex items-center justify-center text-warning">
            <i className="fa-solid fa-key text-xs" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-foreground">Password</h2>
            <p className="text-[11px] text-muted-foreground">Use at least 6 characters. You won't be signed out on other devices automatically.</p>
          </div>
        </header>
        <div className="px-6 py-5 space-y-4">
          <Field
            label="New password"
            value={passwordForm.newPassword}
            placeholder="••••••••"
            onChange={(v) => setPasswordForm(p => ({ ...p, newPassword: v }))}
            type="password"
          />
          <Field
            label="Confirm new password"
            value={passwordForm.confirmPassword}
            placeholder="••••••••"
            onChange={(v) => setPasswordForm(p => ({ ...p, confirmPassword: v }))}
            type="password"
            error={
              passwordForm.confirmPassword.length > 0 &&
              passwordForm.newPassword !== passwordForm.confirmPassword
                ? 'Passwords do not match'
                : undefined
            }
          />
        </div>
        <footer className="px-6 py-3 bg-muted border-t border-border flex items-center justify-end gap-2">
          <button
            onClick={handlePasswordSave}
            disabled={!passwordReady || savingPassword}
            className={`px-4 py-2 rounded-lg text-xs font-bold transition-all ${
              !passwordReady
                ? 'bg-card border border-border text-muted-foreground cursor-not-allowed'
                : 'bg-primary text-primary-foreground hover:bg-primary/90'
            }`}
          >
            {savingPassword ? (<><LogoSpinner size={14} className="mr-1.5 inline-block align-middle" /> Updating…</>) : 'Update password'}
          </button>
        </footer>
      </section>

      {/* Danger zone */}
      <section className="bg-card border border-red-500/30 rounded-2xl overflow-hidden">
        <header className="px-6 py-4 border-b border-red-500/20 flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-red-500/15 border border-red-500/30 flex items-center justify-center text-destructive">
            <i className="fa-solid fa-triangle-exclamation text-xs" />
          </div>
          <div>
            <h2 className="text-sm font-semibold text-foreground">Session</h2>
            <p className="text-[11px] text-muted-foreground">Sign out of AxBi on this device.</p>
          </div>
        </header>
        <div className="px-6 py-4 flex items-center justify-between gap-4">
          <p className="text-xs text-muted-foreground">
            Signed in as <span className="text-foreground font-medium">{originalEmail}</span>
          </p>
          <button
            onClick={handleSignOut}
            disabled={signingOut}
            className="px-4 py-2 rounded-lg bg-red-700 text-white text-xs font-bold hover:bg-red-800 transition-colors"
          >
            {signingOut ? (<><LogoSpinner size={14} className="mr-1.5 inline-block align-middle" /> Signing out…</>) : (<><i className="fa-solid fa-right-from-bracket mr-1.5" /> Sign out</>)}
          </button>
        </div>
      </section>
    </div>
  )
}

interface FieldProps {
  label: string
  value: string
  onChange: (v: string) => void
  placeholder?: string
  hint?: string
  type?: 'text' | 'email' | 'password'
  error?: string
}

function Field({ label, value, onChange, placeholder, hint, type = 'text', error }: FieldProps) {
  return (
    <div>
      <label className="block text-[11px] uppercase tracking-wider text-muted-foreground font-semibold mb-1.5">
        {label}
      </label>
      <input
        type={type}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className={`w-full px-3.5 py-2.5 rounded-lg bg-muted border ${
          error ? 'border-red-500/50' : 'border-border focus:border-primary/60'
        } text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:ring-2 focus:ring-primary/30 transition-all`}
      />
      {error && <p className="text-[11px] text-destructive mt-1">{error}</p>}
      {!error && hint && <p className="text-[11px] text-muted-foreground mt-1">{hint}</p>}
    </div>
  )
}
