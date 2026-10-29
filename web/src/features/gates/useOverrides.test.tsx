/** R22 (gate-overrides follow-up): useOverrides subscribes only when it has a scope, and tolerates a gateway mock without subscribe. */
import { renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useOverrides } from './useOverrides';

const gw = vi.hoisted(() => ({ value: {} as Record<string, unknown> }));
vi.mock('@/contexts/GatewayContext', () => ({ useGateway: () => gw.value }));

describe('useOverrides subscription (R22)', () => {
  it('does not subscribe when neither agentId nor capability is set', () => {
    const subscribe = vi.fn(() => () => {});
    gw.value = { rpc: vi.fn().mockResolvedValue({ overrides: [] }), subscribe };
    renderHook(() => useOverrides({}));
    expect(subscribe).not.toHaveBeenCalled();
  });

  it('subscribes when an agentId is set', () => {
    const subscribe = vi.fn(() => () => {});
    gw.value = { rpc: vi.fn().mockResolvedValue({ overrides: [] }), subscribe };
    renderHook(() => useOverrides({ agentId: 'drafter' }));
    expect(subscribe).toHaveBeenCalledTimes(1);
  });

  it('tolerates a gateway without subscribe', async () => {
    const rpc = vi.fn().mockResolvedValue({ overrides: [] });
    gw.value = { rpc };
    renderHook(() => useOverrides({ capability: 'catalog.tag' }));
    await waitFor(() => expect(rpc).toHaveBeenCalledWith('overrides.list', { capability: 'catalog.tag' }));
  });
});
