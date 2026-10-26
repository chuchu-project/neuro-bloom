using UnityEngine;
using UnityEditor;

// 临时诊断：搞清楚"光纹环为什么一个像素都没画出来"
public static class NeuroDiagRune
{
    [MenuItem("NeuroBloom/诊断光纹环")]
    public static void Run()
    {
        Dump(AssetsPath("Materials/NB2_Rune.mat"));
        Dump(AssetsPath("Materials/NB2_Beam.mat"));

        var cam = Camera.main;
        if (cam == null) { Debug.Log("[DIAG] 没有 Main Camera"); return; }

        var planes = GeometryUtility.CalculateFrustumPlanes(cam);
        Debug.Log("[DIAG] cam pos=" + cam.transform.position + " euler=" + cam.transform.eulerAngles
                  + " cullingMask=" + cam.cullingMask + " clearFlags=" + cam.clearFlags
                  + " near=" + cam.nearClipPlane + " far=" + cam.farClipPlane);

        int hit = 0, total = 0, additive = 0;
        var all = Object.FindObjectsByType<MeshRenderer>(FindObjectsSortMode.None);
        foreach (var r in all)
        {
            total++;
            bool fr = GeometryUtility.TestPlanesAABB(planes, r.bounds);
            string mn = (r.sharedMaterial != null) ? r.sharedMaterial.name : "";
            bool isAdd = mn.StartsWith("NB2_Beam") || mn.StartsWith("NB2_Rune") || mn.StartsWith("NB2_Burst");
            if (isAdd)
            {
                additive++;
                Debug.Log("[DIAG] ADD " + r.name
                          + " | goActive=" + r.gameObject.activeInHierarchy
                          + " rEnabled=" + r.enabled
                          + " inFrustum=" + fr
                          + " layer=" + r.gameObject.layer
                          + " center=" + r.bounds.center
                          + " size=" + r.bounds.size
                          + " mat=" + mn
                          + " mesh=" + MeshOf(r)
                          + " meshColors=" + MeshColors(r));
            }
            if (r.name.StartsWith("RuneRing") || r.name.StartsWith("BurstRing"))
            {
                hit++;
                Debug.Log("[DIAG] RING " + r.name + " | inFrustum=" + fr + " size=" + r.bounds.size);
            }
        }
        Debug.Log("[DIAG] 全部 MeshRenderer=" + total + "，加法材质=" + additive + "，环=" + hit);
    }

    // 关键怀疑点：Legacy Shaders/Particles/Additive 会把顶点色乘进去。
    // 内置 Quad 的顶点色若是黑/缺省，加出来就是全黑 → 一个像素都看不见。
    private static string MeshColors(MeshRenderer r)
    {
        var mf = r.GetComponent<MeshFilter>();
        if (mf == null || mf.sharedMesh == null) return "noMesh";
        var m = mf.sharedMesh;
        var c = m.colors;
        if (c == null || c.Length == 0) return "无颜色通道(默认白)";
        return "n=" + c.Length + " [0]=" + c[0];
    }

    private static string MeshOf(MeshRenderer r)
    {
        var mf = r.GetComponent<MeshFilter>();
        if (mf == null) return "noFilter";
        if (mf.sharedMesh == null) return "NULL";
        return mf.sharedMesh.name + "(" + mf.sharedMesh.vertexCount + "v)";
    }

    private static string AssetsPath(string rel)
    {
        string root = Application.dataPath.Replace("\\", "/");
        return "Assets/" + rel;
    }

    // 材质替换测试：环到底渲染不出来，还是"加法混合"这条路径有问题？
    [MenuItem("NeuroBloom/诊断-材质替换测试")]
    public static void MaterialSwapTest()
    {
        var rune = GameObject.Find("RuneRing");
        if (rune == null) { Debug.Log("[DIAG2] 场景里找不到 RuneRing"); return; }
        var r = rune.GetComponent<MeshRenderer>();
        var orig = r.sharedMaterial;
        var tex = orig.GetTexture("_MainTex");

        NeuroBloomSceneBuilderV2.RenderTo("diag_A_orig.png");

        string[] names = {
            "Unlit/Color", "Unlit/Transparent", "Sprites/Default", "Standard",
            "Legacy Shaders/Transparent/Diffuse", "Particles/Standard Unlit",
            "Unlit/Texture", "Mobile/Particles/Additive"
        };
        foreach (var n in names)
        {
            var sh = Shader.Find(n);
            if (sh == null) { Debug.Log("[DIAG2] Shader.Find 找不到: " + n); continue; }
            var m = new Material(sh);
            if (m.HasProperty("_MainTex")) m.SetTexture("_MainTex", tex);
            if (m.HasProperty("_Color")) m.SetColor("_Color", new Color(0f, 1f, 0f, 1f));
            if (m.HasProperty("_TintColor")) m.SetColor("_TintColor", new Color(0f, 1f, 0f, 1f));
            r.sharedMaterial = m;
            NeuroBloomSceneBuilderV2.RenderTo("diag_" + n.Replace("/", "_") + ".png");
            Debug.Log("[DIAG2] 渲染 " + n + " supported=" + sh.isSupported + " queue=" + m.renderQueue);
        }

        r.sharedMaterial = orig;
        Debug.Log("[DIAG2] 已还原原材质");
    }

    // 透明队列为什么不渲染？逐个排除：后处理？MSAA？渲染队列？
    [MenuItem("NeuroBloom/诊断-透明队列三连测")]
    public static void TransparentProbe()
    {
        var rune = GameObject.Find("RuneRing");
        if (rune == null) { Debug.Log("[DIAG3] 场景里找不到 RuneRing"); return; }
        var r = rune.GetComponent<MeshRenderer>();
        var orig = r.sharedMaterial;
        var tex = orig.GetTexture("_MainTex");

        var cam = Camera.main;
        var fx = cam.GetComponent<NeuroPostFX>();
        var sh = Shader.Find("Unlit/Transparent");
        var m = new Material(sh);
        if (m.HasProperty("_MainTex")) m.SetTexture("_MainTex", tex);
        if (m.HasProperty("_Color")) m.SetColor("_Color", new Color(0f, 1f, 0f, 1f));
        r.sharedMaterial = m;

        // V0 基准：原样
        Probe(cam, "diag3_V0_默认.png", 4, true);

        // V1 关掉 NeuroPostFX
        Probe(cam, "diag3_V1_无后处理.png", 4, false);

        // V2 关掉 MSAA
        Probe(cam, "diag3_V2_无MSAA.png", 1, true);

        // V3 透明材质塞进不透明队列（2499）
        m.renderQueue = 2499;
        Probe(cam, "diag3_V3_队列2499.png", 4, true);
        m.renderQueue = 3000;

        r.sharedMaterial = orig;
        if (fx != null) fx.enabled = true;
        Debug.Log("[DIAG3] 测试结束，已还原");
    }

    private static void Probe(Camera cam, string file, int aa, bool postFx)
    {
        var fx = cam.GetComponent<NeuroPostFX>();
        if (fx != null) fx.enabled = postFx;

        const int w = 1280, h = 720;
        var rt = new RenderTexture(w, h, 24, RenderTextureFormat.ARGB32);
        rt.antiAliasing = aa;
        rt.Create();

        var old = cam.targetTexture;
        cam.targetTexture = rt;
        cam.Render();
        cam.targetTexture = old;

        var oldActive = RenderTexture.active;
        RenderTexture.active = rt;
        var t = new Texture2D(w, h, TextureFormat.RGB24, false);
        t.ReadPixels(new Rect(0, 0, w, h), 0, 0);
        t.Apply();
        RenderTexture.active = oldActive;
        rt.Release();
        Object.DestroyImmediate(rt);

        string dir = System.IO.Path.GetFullPath(System.IO.Path.Combine(Application.dataPath, "../render_states"));
        System.IO.Directory.CreateDirectory(dir);
        System.IO.File.WriteAllBytes(System.IO.Path.Combine(dir, file), t.EncodeToPNG());
        Object.DestroyImmediate(t);

        if (fx != null) fx.enabled = true;
        Debug.Log("[DIAG3] 出图 " + file + " (aa=" + aa + " postFx=" + postFx + ")");
    }

    private static void Dump(string path)
    {
        var m = AssetDatabase.LoadAssetAtPath<Material>(path);
        if (m == null) { Debug.Log("[DIAG] 材质读不到: " + path); return; }
        var sh = m.shader;
        var tex = m.HasProperty("_MainTex") ? m.GetTexture("_MainTex") : null;
        Debug.Log("[DIAG] mat=" + m.name
                  + " shader=" + (sh != null ? sh.name : "null")
                  + " supported=" + (sh != null && sh.isSupported)
                  + " queue=" + m.renderQueue
                  + " hasTint=" + m.HasProperty("_TintColor")
                  + " tint=" + (m.HasProperty("_TintColor") ? m.GetColor("_TintColor").ToString() : "-")
                  + " mainTex=" + (tex != null ? tex.name : "null"));
    }
    // 隔离测试：相机正前方 4 米放一块透明四边形，排除雾/距离/遮挡
    [MenuItem("NeuroBloom/诊断-近距透明测试")]
    public static void NearTransparentTest()
    {
        var cam = Camera.main;
        Debug.Log("[DIAG4] fog=" + RenderSettings.fog + " mode=" + RenderSettings.fogMode
                  + " density=" + RenderSettings.fogDensity
                  + " linear=" + RenderSettings.fogStartDistance + "~" + RenderSettings.fogEndDistance
                  + " color=" + RenderSettings.fogColor);

        var q = GameObject.CreatePrimitive(PrimitiveType.Quad);
        q.name = "DIAGQ";
        q.transform.SetParent(null);
        q.transform.position = cam.transform.position + cam.transform.forward * 4f;
        q.transform.rotation = cam.transform.rotation;
        q.transform.localScale = new Vector3(2f, 2f, 1f);
        var qr = q.GetComponent<MeshRenderer>();

        var tex = MakeDiskTex();

        Probe(cam, "diag4_Z_无四边形.png", 4, true);

        var mo = new Material(Shader.Find("Unlit/Color"));
        mo.SetColor("_Color", new Color(1f, 0f, 1f, 1f));
        qr.sharedMaterial = mo;
        Probe(cam, "diag4_A_不透明.png", 4, true);

        var mt = new Material(Shader.Find("Unlit/Transparent"));
        mt.SetTexture("_MainTex", tex);
        qr.sharedMaterial = mt;
        Probe(cam, "diag4_B_UnlitTransparent.png", 4, true);

        var ms = new Material(Shader.Find("Sprites/Default"));
        ms.SetTexture("_MainTex", tex);
        ms.SetColor("_Color", new Color(1f, 0f, 1f, 0.7f));
        qr.sharedMaterial = ms;
        Probe(cam, "diag4_C_Sprites.png", 4, true);

        var mp = new Material(Shader.Find("Legacy Shaders/Particles/Additive"));
        mp.SetTexture("_MainTex", tex);
        mp.SetColor("_TintColor", new Color(1f, 0f, 1f, 0.7f));
        qr.sharedMaterial = mp;
        Probe(cam, "diag4_D_粒子加法.png", 4, true);

        var fx = cam.GetComponent<NeuroPostFX>();
        bool fog = RenderSettings.fog;
        RenderSettings.fog = false;
        Probe(cam, "diag4_E_加法无雾.png", 4, true);
        RenderSettings.fog = fog;

        Object.DestroyImmediate(q);
        Debug.Log("[DIAG4] 测试结束");
    }

    // 诊断入口：调 builder 的贴图检查
    [MenuItem("NeuroBloom/诊断-贴图alpha")]
    public static void TexAlphaCheck()
    {
        NeuroBloomSceneBuilderV2.DebugRuneTex();
    }

    private static Texture2D MakeDiskTex()
    {
        const int s = 64;
        var t = new Texture2D(s, s, TextureFormat.RGBA32, false);
        var px = new Color32[s * s];
        for (int y = 0; y < s; y++)
            for (int x = 0; x < s; x++)
            {
                float u = (x + .5f) / s - .5f, v = (y + .5f) / s - .5f;
                float r = Mathf.Sqrt(u * u + v * v);
                float a = Mathf.Clamp01(1f - r / 0.45f);
                px[y * s + x] = new Color32(255, 255, 255, (byte)Mathf.RoundToInt(a * 255f));
            }
        t.SetPixels32(px);
        t.wrapMode = TextureWrapMode.Clamp;
        t.Apply();
        return t;
    }
}
