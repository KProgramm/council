import { useCallback, useEffect, useLayoutEffect, useRef, useState, type RefObject } from 'react'

const SIZE = 48
const HOP_MS = 1300
const FLIGHT_MS = 750

type Props = { listRef: RefObject<HTMLElement | null>; thinking: boolean[]; busy: boolean }

// Sprite: Owl and Raven Sprites by Revangale (opengameart.org/content/owl-and-raven-sprites).
export default function AgentOwl({ listRef, thinking, busy }: Props) {
  const [target, setTarget] = useState(0)
  const [flying, setFlying] = useState(false)
  const [y, setY] = useState<number | null>(null)
  const thinkingRef = useRef(thinking)
  useEffect(() => { thinkingRef.current = thinking }, [thinking])

  const place = useCallback(() => {
    const row = listRef.current?.children[target] as HTMLElement | undefined
    const head = row?.firstElementChild as HTMLElement | undefined
    if (row && head) setY(row.offsetTop + head.offsetHeight / 2 - SIZE / 2)
  }, [listRef, target])

  useLayoutEffect(place, [place])
  useEffect(() => {
    const list = listRef.current
    if (!list) return
    const observer = new ResizeObserver(place)
    observer.observe(list)
    Array.from(list.children).forEach((row) => observer.observe(row))
    return () => observer.disconnect()
  }, [listRef, place])

  useEffect(() => {
    if (!busy) return
    let landing = 0
    const hop = () => {
      setTarget((current) => {
        const all = thinkingRef.current.map((_, index) => index)
        const waiting = all.filter((index) => thinkingRef.current[index] && index !== current)
        const pool = waiting.length ? waiting : all.filter((index) => index !== current)
        return pool[Math.floor(Math.random() * pool.length)]
      })
      setFlying(true)
      window.clearTimeout(landing)
      landing = window.setTimeout(() => setFlying(false), FLIGHT_MS)
    }
    hop()
    const interval = window.setInterval(hop, HOP_MS)
    return () => { window.clearInterval(interval); window.clearTimeout(landing); setFlying(false) }
  }, [busy])

  return <div aria-hidden="true" className="pointer-events-none absolute right-0 top-0 transition-transform ease-in-out motion-reduce:transition-none"
    style={{ width: SIZE, height: SIZE, transform: `translateY(${y ?? 0}px)`, transitionDuration: `${FLIGHT_MS}ms`, opacity: y === null ? 0 : 1 }}>
    <div className={flying ? 'owl-sprite owl-flying' : 'owl-sprite owl-perched'}/>
  </div>
}
