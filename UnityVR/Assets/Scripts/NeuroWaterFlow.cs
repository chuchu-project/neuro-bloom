// NeuroWaterFlow.cs —— 溪流/水面的涟漪与波光。
// 挂在水平面的 GameObject 上（带 MeshRenderer 的 Plane）。
//
// 做法：不改材质实例（避免编辑器里泄漏材质并弄脏场景），
// 而是用 MaterialPropertyBlock 每帧覆写 _MainTex_ST 的 uv 偏移，
// 让水面噪声贴图缓慢流动；同时让水面本身做极轻微的法线向上下浮动。
//
// 明文依赖：材质必须使用 Standard（或任何支持 _MainTex_ST 的）着色器。
using UnityEngine;

[ExecuteAlways]
[AddComponentMenu("NeuroBloom/Water Flow")]
public class NeuroWaterFlow : MonoBehaviour
{
    [Header("贴图流动（uv/秒）")]
    public float scrollX = 0.035f;
    public float scrollY = 0.018f;
    public float tiling = 3.0f;

    [Header("水面起伏")]
    public float bobAmplitude = 0.025f;
    public float bobSpeed = 0.55f;

    private Renderer _r;
    private MaterialPropertyBlock _mpb;
    private Vector3 _basePos;
    private bool _init;

    private void OnEnable()
    {
        Cache();
    }

    private void Cache()
    {
        if (_r == null) _r = GetComponent<Renderer>();
        _basePos = transform.localPosition;
        if (_mpb == null) _mpb = new MaterialPropertyBlock();
        _init = true;
    }

    private void Update()
    {
        if (!_init || _r == null) Cache();
        if (_r == null) return;

        float t = Time.realtimeSinceStartup;

        Vector4 st = new Vector4(tiling, tiling, t * scrollX, t * scrollY);

        _r.GetPropertyBlock(_mpb);
        _mpb.SetVector("_MainTex_ST", st);
        _r.SetPropertyBlock(_mpb);

        Vector3 p = _basePos;
        p.y += Mathf.Sin(t * bobSpeed) * bobAmplitude;
        transform.localPosition = p;
    }
}
