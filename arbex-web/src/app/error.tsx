"use client"

import { useEffect } from 'react'

export default function Error({
  error,
  reset,
}: {
  error: Error & { digest?: string }
  reset: () => void
}) {
  useEffect(() => {
    // Log the error to an error reporting service
    console.error("Next.js App Error Boundary caught:", error)
  }, [error])

  return (
    <div className="flex h-screen w-full items-center justify-center bg-[#0a0a0a] text-white">
      <div className="text-center space-y-4">
        <h2 className="text-xl font-semibold text-red-500">Something went wrong!</h2>
        <p className="text-gray-400 font-mono text-sm max-w-md mx-auto">{error.message}</p>
        <button
          onClick={() => reset()}
          className="mt-4 rounded-md bg-blue-600 px-4 py-2 text-sm hover:bg-blue-500"
        >
          Try again
        </button>
      </div>
    </div>
  )
}
