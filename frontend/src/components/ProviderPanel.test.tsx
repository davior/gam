import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ProviderPanel from '@/components/ProviderPanel'
import { providersApi, type AIProvider } from '@/api/providers'

function provider(overrides: Partial<AIProvider> = {}): AIProvider {
  return {
    id: 'p1',
    name: 'Claude',
    provider_type: 'anthropic',
    base_url: null,
    model: 'claude-sonnet-4-20250514',
    max_tokens: 64000,
    supports_images: true,
    use_anthropic_api: false,
    extra_params: null,
    enabled: true,
    is_active: true,
    api_key_configured: true,
    ...overrides,
  }
}

beforeEach(() => {
  vi.spyOn(providersApi, 'list').mockResolvedValue([])
})

afterEach(() => {
  vi.restoreAllMocks()
})

describe('ProviderPanel', () => {
  it('says plainly when nothing is configured', async () => {
    render(<ProviderPanel />)

    expect(await screen.findByText(/no provider configured yet/i)).toBeInTheDocument()
  })

  it('lists what is configured, and which one is in use', async () => {
    vi.spyOn(providersApi, 'list').mockResolvedValue([
      provider(),
      provider({
        id: 'p2',
        name: 'DeepSeek',
        provider_type: 'deepseek',
        model: 'deepseek-chat',
        supports_images: false,
        is_active: false,
      }),
    ])
    render(<ProviderPanel />)

    expect(await screen.findByText('Claude')).toBeInTheDocument()
    expect(screen.getByText('DeepSeek')).toBeInTheDocument()
    expect(screen.getByText(/^active$/i)).toBeInTheDocument()
    // Only the inactive one offers to be switched to.
    expect(screen.getAllByRole('button', { name: /use this one/i })).toHaveLength(1)
  })

  it('keeps the form closed until asked', async () => {
    // Load-bearing beyond tidiness: SettingsView already renders two panels with an
    // "API key" field, and a third always-present one makes every label query in
    // SettingsView.test.tsx ambiguous.
    render(<ProviderPanel />)
    await screen.findByText(/no provider configured yet/i)

    expect(screen.queryByLabelText(/provider api key/i)).toBeNull()

    await userEvent.click(screen.getByRole('button', { name: /add a provider/i }))
    expect(await screen.findByLabelText(/provider api key/i)).toBeInTheDocument()
  })

  it('creates a provider from the form', async () => {
    const create = vi.spyOn(providersApi, 'create').mockResolvedValue(provider())
    render(<ProviderPanel />)
    await screen.findByText(/no provider configured yet/i)

    await userEvent.click(screen.getByRole('button', { name: /add a provider/i }))
    await userEvent.type(screen.getByLabelText(/^name$/i), 'Claude')
    await userEvent.type(
      screen.getByLabelText(/^model name$/i),
      'claude-sonnet-4-20250514'
    )
    await userEvent.type(screen.getByLabelText(/provider api key/i), 'sk-ant-secret')
    await userEvent.click(screen.getByRole('button', { name: /save provider/i }))

    await waitFor(() => expect(create).toHaveBeenCalled())
    expect(create.mock.calls[0][0]).toMatchObject({
      name: 'Claude',
      provider_type: 'anthropic',
      model: 'claude-sonnet-4-20250514',
      api_key: 'sk-ant-secret',
    })
  })

  it('never seeds the key field from the server', async () => {
    // It was never sent one. An empty box on edit means "leave the stored key alone",
    // which is also what the request means by omitting it.
    vi.spyOn(providersApi, 'list').mockResolvedValue([provider()])
    render(<ProviderPanel />)

    await userEvent.click(await screen.findByRole('button', { name: /edit claude/i }))

    expect(screen.getByLabelText(/provider api key/i)).toHaveValue('')
    expect(screen.getByLabelText(/provider api key/i)).toHaveAttribute(
      'placeholder',
      expect.stringMatching(/keep the stored key/i)
    )
  })

  it('omits the key on edit unless one was typed', async () => {
    vi.spyOn(providersApi, 'list').mockResolvedValue([provider()])
    const update = vi.spyOn(providersApi, 'update').mockResolvedValue(provider())
    render(<ProviderPanel />)

    await userEvent.click(await screen.findByRole('button', { name: /edit claude/i }))
    await userEvent.clear(screen.getByLabelText(/^name$/i))
    await userEvent.type(screen.getByLabelText(/^name$/i), 'Claude Opus')
    await userEvent.click(screen.getByRole('button', { name: /save provider/i }))

    await waitFor(() => expect(update).toHaveBeenCalled())
    expect(update.mock.calls[0][1]).not.toHaveProperty('api_key')
    expect(update.mock.calls[0][1]).toMatchObject({ name: 'Claude Opus' })
  })

  it('offers the Anthropic-compatible endpoint only where it exists', async () => {
    render(<ProviderPanel />)
    await screen.findByText(/no provider configured yet/i)
    await userEvent.click(screen.getByRole('button', { name: /add a provider/i }))

    const anthropicOption = /use the anthropic-compatible endpoint/i
    // Anthropic is already on that protocol, so there is nothing to opt into.
    expect(screen.queryByLabelText(anthropicOption)).toBeNull()

    await userEvent.selectOptions(screen.getByLabelText(/^provider type$/i), 'deepseek')
    expect(screen.getByText(anthropicOption)).toBeInTheDocument()

    // Ollama speaks only its own protocol, and its address may be private — pointing a
    // Messages request at it would mean pointing it inside the network.
    await userEvent.selectOptions(screen.getByLabelText(/^provider type$/i), 'ollama')
    expect(screen.queryByText(anthropicOption)).toBeNull()
  })

  it('pre-ticks whether the model can see, by type', async () => {
    render(<ProviderPanel />)
    await screen.findByText(/no provider configured yet/i)
    await userEvent.click(screen.getByRole('button', { name: /add a provider/i }))

    const seesImages = () => screen.getByLabelText(/this model can look at images/i)
    expect(seesImages()).toBeChecked()

    await userEvent.selectOptions(screen.getByLabelText(/^provider type$/i), 'deepseek')
    expect(seesImages()).not.toBeChecked()
  })

  it('asks for a key everywhere except Ollama', async () => {
    render(<ProviderPanel />)
    await screen.findByText(/no provider configured yet/i)
    await userEvent.click(screen.getByRole('button', { name: /add a provider/i }))

    expect(screen.getByLabelText(/provider api key/i)).toBeInTheDocument()

    await userEvent.selectOptions(screen.getByLabelText(/^provider type$/i), 'ollama')
    expect(screen.queryByLabelText(/provider api key/i)).toBeNull()
    expect(screen.getByLabelText(/base url/i)).toBeInTheDocument()
  })

  it('refuses extra parameters that are not a JSON object', async () => {
    const create = vi.spyOn(providersApi, 'create').mockResolvedValue(provider())
    render(<ProviderPanel />)
    await screen.findByText(/no provider configured yet/i)

    await userEvent.click(screen.getByRole('button', { name: /add a provider/i }))
    await userEvent.type(screen.getByLabelText(/^name$/i), 'Claude')
    await userEvent.type(
      screen.getByLabelText(/^model name$/i),
      'claude-sonnet-4-20250514'
    )
    // Valid JSON, wrong shape — the case a bare `JSON.parse` check would let through.
    await userEvent.type(screen.getByLabelText(/extra parameters/i), '"temperature"')
    await userEvent.click(screen.getByRole('button', { name: /save provider/i }))

    expect(await screen.findByText(/must be a json object/i)).toBeInTheDocument()
    expect(create).not.toHaveBeenCalled()
  })

  it('reports what a connection test found', async () => {
    vi.spyOn(providersApi, 'test').mockResolvedValue({
      success: false,
      message: 'The API key was rejected',
    })
    render(<ProviderPanel />)
    await screen.findByText(/no provider configured yet/i)

    await userEvent.click(screen.getByRole('button', { name: /add a provider/i }))
    await userEvent.type(
      screen.getByLabelText(/^model name$/i),
      'claude-sonnet-4-20250514'
    )
    await userEvent.click(screen.getByRole('button', { name: /test connection/i }))

    expect(await screen.findByText(/the api key was rejected/i)).toBeInTheDocument()
  })

  it('tests a saved provider by id, so the stored key is used', async () => {
    vi.spyOn(providersApi, 'list').mockResolvedValue([provider()])
    const test = vi
      .spyOn(providersApi, 'test')
      .mockResolvedValue({ success: true, message: 'Connected' })
    render(<ProviderPanel />)

    await userEvent.click(await screen.findByRole('button', { name: /edit claude/i }))
    await userEvent.click(screen.getByRole('button', { name: /test connection/i }))

    await waitFor(() => expect(test).toHaveBeenCalled())
    expect(test.mock.calls[0][0]).toMatchObject({ provider_id: 'p1' })
  })

  it('switches the active provider', async () => {
    vi.spyOn(providersApi, 'list').mockResolvedValue([
      provider({ id: 'p2', name: 'DeepSeek', is_active: false }),
    ])
    const activate = vi.spyOn(providersApi, 'activate').mockResolvedValue(provider())
    render(<ProviderPanel />)

    await userEvent.click(await screen.findByRole('button', { name: /use this one/i }))

    await waitFor(() => expect(activate).toHaveBeenCalledWith('p2'))
  })

  it('shows no price, because this panel is configuration', async () => {
    // Not because GAM refuses to show costs — it has a pricing table now and its own
    // panel for spend. This one is about which model to use, and a figure here would be
    // answering a question nobody asked while configuring a provider.
    vi.spyOn(providersApi, 'list').mockResolvedValue([provider()])
    render(<ProviderPanel />)

    await screen.findByText('Claude')
    expect(screen.queryByText(/\$/)).toBeNull()
  })
})
