// Supabase client utilities for Edge Functions

// @ts-ignore: Deno ESM import
import { createClient } from 'https://esm.sh/@supabase/supabase-js@2';

declare const Deno: {
  env: {
    get(key: string): string | undefined;
  };
};

// Client with user's auth (respects RLS)
export function createUserClient(authHeader: string) {
  return createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_ANON_KEY')!,
    {
      global: {
        headers: { Authorization: authHeader },
      },
    }
  );
}

// Admin client (bypasses RLS - use carefully!)
export function createAdminClient() {
  return createClient(
    Deno.env.get('SUPABASE_URL')!,
    Deno.env.get('SUPABASE_SERVICE_ROLE_KEY')!
  );
}

// Get user from auth header
export async function getUser(authHeader: string) {
  const client = createUserClient(authHeader);
  const { data: { user }, error } = await client.auth.getUser();
  if (error || !user) {
    throw new Error('Unauthorized');
  }
  return user;
}
