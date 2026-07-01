import { LogoLoader } from './LogoLoader'

interface Props {
  message?: string
}

export function FullPageLoader({ message = 'Loading' }: Props) {
  return <LogoLoader message={message} />
}

export default FullPageLoader
