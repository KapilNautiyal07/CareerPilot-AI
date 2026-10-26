"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useQueryClient } from "@tanstack/react-query";
import { GraduationCap } from "lucide-react";
import Link from "next/link";
import { useRouter, useSearchParams } from "next/navigation";
import { useState } from "react";
import { useForm } from "react-hook-form";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Alert, Card, CardContent, CardDescription, CardHeader, CardTitle, Field, Input } from "@/components/ui/primitives";
import { api, errorMessage } from "@/lib/api";
import { keys } from "@/lib/queries";
import type { User } from "@/types/api";

const loginSchema = z.object({
  email: z.email("Enter a valid email"),
  password: z.string().min(1, "Enter your password"),
});
const signupSchema = z.object({
  full_name: z.string().max(200).optional(),
  email: z.email("Enter a valid email"),
  password: z
    .string()
    .min(10, "Use at least 10 characters")
    .max(72, "Use at most 72 characters")
    .regex(/[A-Za-z]/, "Include a letter")
    .regex(/\d/, "Include a number"),
});

type Values = { email: string; password: string; full_name?: string };

export function AuthForm({ mode }: { mode: "login" | "signup" }) {
  const router = useRouter();
  const params = useSearchParams();
  const qc = useQueryClient();
  const [serverError, setServerError] = useState<string | null>(null);
  const form = useForm<Values>({ resolver: zodResolver(mode === "login" ? loginSchema : signupSchema) });
  const { errors, isSubmitting } = form.formState;

  async function onSubmit(values: Values) {
    setServerError(null);
    try {
      const res = await api<{ user: User }>(mode === "login" ? "/auth/login" : "/auth/register", { body: values });
      qc.setQueryData(keys.me, res.user);
      const next = params.get("next");
      const safeNext = next && next.startsWith("/") && !next.startsWith("//") ? next : "/dashboard";
      router.replace(res.user.onboarding_completed ? safeNext : "/onboarding");
    } catch (err) {
      setServerError(errorMessage(err));
    }
  }

  return (
    <div className="flex min-h-dvh items-center justify-center px-4 py-10">
      <div className="w-full max-w-sm">
        <Link href="/" className="mb-6 flex items-center justify-center gap-2 font-semibold">
          <span className="grid size-7 place-items-center rounded-md bg-primary text-primary-foreground">
            <GraduationCap className="size-4" aria-hidden />
          </span>
          CareerPilot AI
        </Link>
        <Card>
          <CardHeader>
            <CardTitle>{mode === "login" ? "Welcome back" : "Create your account"}</CardTitle>
            <CardDescription>
              {mode === "login" ? "Log in to continue your preparation." : "It takes about two minutes to set up."}
            </CardDescription>
          </CardHeader>
          <CardContent>
            {/* method="post": if submitted before hydration, credentials must never land in the URL. */}
            <form method="post" onSubmit={form.handleSubmit(onSubmit)} className="space-y-4" noValidate>
              {mode === "signup" && (
                <Field label="Full name" htmlFor="full_name" error={errors.full_name?.message}>
                  <Input id="full_name" autoComplete="name" {...form.register("full_name")} />
                </Field>
              )}
              <Field label="Email" htmlFor="email" error={errors.email?.message}>
                <Input
                  id="email"
                  type="email"
                  autoComplete="email"
                  aria-invalid={!!errors.email}
                  aria-describedby={errors.email ? "email-error" : undefined}
                  {...form.register("email")}
                />
              </Field>
              <Field
                label="Password"
                htmlFor="password"
                error={errors.password?.message}
                hint={mode === "signup" ? "At least 10 characters with letters and numbers." : undefined}
              >
                <Input
                  id="password"
                  type="password"
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  aria-invalid={!!errors.password}
                  aria-describedby={errors.password ? "password-error" : undefined}
                  {...form.register("password")}
                />
              </Field>
              {serverError && <Alert tone="error">{serverError}</Alert>}
              <Button type="submit" className="w-full" loading={isSubmitting}>
                {mode === "login" ? "Log in" : "Create account"}
              </Button>
            </form>
          </CardContent>
        </Card>
        <p className="mt-4 text-center text-sm text-muted-foreground">
          {mode === "login" ? (
            <>
              New here?{" "}
              <Link href="/signup" className="font-medium text-primary hover:underline">
                Create an account
              </Link>
            </>
          ) : (
            <>
              Already have an account?{" "}
              <Link href="/login" className="font-medium text-primary hover:underline">
                Log in
              </Link>
            </>
          )}
        </p>
      </div>
    </div>
  );
}
