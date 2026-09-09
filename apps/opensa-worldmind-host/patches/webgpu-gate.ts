/**
 * SPECTER graphics capability gate.
 *
 * Order is deliberate for older Intel hardware:
 *   1) WebGPU core (modern backend)
 *   2) WebGPU compatibility feature level (D3D11 / GLES-class backends when exposed)
 *   3) WebGL2 legacy renderer
 *
 * The legacy path is hosted from a known-good historical OpenSA Three/WebGL build.
 */

/** Result of probing the graphics stack. `ok` is WebGPU core for upstream compatibility. */
export type WebGpuProbe = 'compatibility' | 'no-adapter' | 'no-api' | 'ok' | 'webgl2';

type GpuLike = Pick<GPU, 'requestAdapter'>;
type FeatureLevel = 'compatibility' | 'core';
type AdapterOptionsWithLevel = GPURequestAdapterOptions & { featureLevel?: FeatureLevel };

async function requestLevel(
  gpu: GpuLike,
  featureLevel: FeatureLevel,
  powerPreference?: GPUPowerPreference,
): Promise<GPUAdapter | null> {
  try {
    const options: AdapterOptionsWithLevel = {
      featureLevel,
      ...(powerPreference ? { powerPreference } : {}),
    };

    return await gpu.requestAdapter(options);
  } catch {
    return null;
  }
}

/** Tiny, side-effect-free WebGL2 availability probe used only after both WebGPU asks fail. */
export function probeWebGl2(): boolean {
  if (typeof document === 'undefined') {
    return false;
  }
  try {
    const canvas = document.createElement('canvas');
    const gl = canvas.getContext('webgl2');
    if (!gl) {
      return false;
    }
    gl.getExtension('WEBGL_lose_context')?.loseContext();

    return true;
  } catch {
    return false;
  }
}

/**
 * Probe in strict priority order. Core is attempted explicitly first; compatibility is only attempted
 * when core returns null/throws. This matters on old Intel iGPUs where Chrome can expose WebGPU through
 * the compatibility feature level even when the modern core backend cannot be created.
 */
export async function probeWebGpu(
  gpu: GpuLike | null | undefined = typeof navigator === 'undefined' ? undefined : navigator.gpu,
): Promise<WebGpuProbe> {
  if (gpu) {
    const core = await requestLevel(gpu, 'core', 'high-performance');
    if (core) {
      return 'ok';
    }

    // Do not force a power preference here: on single/old integrated GPUs the browser should choose the
    // broadest compatibility adapter it can actually instantiate.
    const compatibility = await requestLevel(gpu, 'compatibility');
    if (compatibility) {
      return 'compatibility';
    }
  }

  if (probeWebGl2()) {
    return 'webgl2';
  }

  return gpu ? 'no-adapter' : 'no-api';
}

/** What the terminal fallback screen says when neither WebGPU nor WebGL2 is available. */
export function webGpuGateMessage(probe: 'no-adapter' | 'no-api'): string {
  return probe === 'no-api'
    ? 'This browser exposes neither a usable WebGPU adapter nor WebGL2. The game cannot create a graphics context on this device.'
    : 'WebGPU core and compatibility mode were both refused, and WebGL2 is unavailable. Update the GPU driver or use a browser/device with WebGL2 support.';
}
