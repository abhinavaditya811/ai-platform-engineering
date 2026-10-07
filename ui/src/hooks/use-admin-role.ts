import { useSession } from 'next-auth/react';
import { useEffect,useState } from 'react';

/**
 * Hook to check admin role.
 *
 * Returns:
 * - `isAdmin`: true when user has admin role (via OIDC group, bootstrap env,
 *   or MongoDB fallback)
 * - `loading`: true while role check is in progress
 *
 * All authenticated users can view the Admin dashboard (read-only).
 * Only admins can perform write operations (role changes, team CRUD, etc.).
 */
export function useAdminRole() {
  const { data: session } = useSession();
  const [isAdmin, setIsAdmin] = useState(false);
  const [loading, setLoading] = useState(true);

  const canViewAdmin = (session?.canViewAdmin === true);
  const canAccessDynamicAgents = (session?.canAccessDynamicAgents === true);

  useEffect(() => {
    async function checkAdminRole() {
      if (!session) {
        setIsAdmin(false);
        setLoading(false);
        return;
      }

      if (session.role === 'admin') {
        setIsAdmin(true);
        setLoading(false);
        return;
      }

      try {
        const response = await fetch('/api/auth/role');
        const data = await response.json();
        setIsAdmin(data.role === 'admin');
      } catch (error) {
        console.warn('[useAdminRole] Failed to check MongoDB role:', error);
        setIsAdmin(false);
      } finally {
        setLoading(false);
      }
    }

    checkAdminRole();
  }, [session]);

  return { isAdmin, canViewAdmin, canAccessDynamicAgents, loading };
}
