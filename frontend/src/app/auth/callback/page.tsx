"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { CheckCircle2, XCircle } from "lucide-react";
import { useAuth } from "@/components/AuthProvider";

function OAuthCallbackContent() {
  const { refresh } = useAuth();
  const router = useRouter();
  const params = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const status = params?.get("status");

  useEffect(() => {
    if (status !== "success") {
      setError("The sign-in provider did not complete the connection. Please try again.");
      return;
    }
    void refresh().then(() => {
      const destination = params?.get("return_to");
      router.replace(destination && destination.startsWith("/") && !destination.startsWith("//") ? destination : "/");
    });
  }, [params, refresh, router, status]);

  return (
    <main className="grid min-h-screen place-items-center bg-[#f8f8f6] p-6">
      <div className="w-full max-w-sm rounded-2xl border border-black/10 bg-white p-7 text-center shadow-sm">
        {error ? <XCircle className="mx-auto h-9 w-9 text-red-500" /> : <CheckCircle2 className="mx-auto h-9 w-9 text-green-600" />}
        <h1 className="mt-4 text-xl font-semibold">{error ? "Sign-in was not completed" : "Securing your workspace"}</h1>
        <p className="mt-2 text-sm leading-6 text-[#6a6a67]">{error || "Finishing your Onsite48AI session…"}</p>
        {error && <button onClick={() => router.replace("/")} className="mt-5 rounded-lg bg-[#161616] px-4 py-2 text-sm font-medium text-white">Return to sign in</button>}
      </div>
    </main>
  );
}

export default function OAuthCallbackPage() {
  return (
    <Suspense fallback={<main className="grid min-h-screen place-items-center bg-[#f8f8f6] text-sm text-[#6a6a67]">Preparing secure sign-in…</main>}>
      <OAuthCallbackContent />
    </Suspense>
  );
}