// NeuroBloomSceneBuilder.cs —— 一键搭建 NeuroBloom 演示场景（花园版）
// 用法：Unity 菜单栏 -> NeuroBloom -> 一键搭建场景
// 自动创建：程序化天空 / 起伏草地 / 池塘 / 石板路 / 树林 / 岩石 / 立体花朵 / 水晶簇 / 萤火虫 / 管理器 / 引导语
using System.Collections.Generic;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

public static class NeuroBloomSceneBuilder
{
    private static System.Random _rnd;

    [MenuItem("NeuroBloom/一键搭建场景")]
    public static void Build()
    {
        _rnd = new System.Random(20240513);
        var scene = EditorSceneManager.NewScene(NewSceneSetup.EmptyScene, NewSceneMode.Single);

        // ---------- 天空盒（程序化，随焦虑变色） ----------
        var skyMat = MakeSkybox();
        RenderSettings.skybox = skyMat;
        RenderSettings.fog = false;                    // 运行时由控制器开启
        RenderSettings.ambientLight = new Color(0.42f, 0.46f, 0.52f);

        // ---------- 光照 ----------
        var sunGO = new GameObject("Sun");
        var sun = sunGO.AddComponent<Light>();
        sun.type = LightType.Directional;
        sun.intensity = 1.2f;
        sun.color = new Color(1f, 0.96f, 0.85f);
        sun.shadows = LightShadows.Soft;
        sun.shadowStrength = 0.55f;
        sunGO.transform.rotation = Quaternion.Euler(46f, -34f, 0f);

        var fillGO = new GameObject("FillLight");
        var fill = fillGO.AddComponent<Light>();
        fill.type = LightType.Directional;
        fill.intensity = 0.35f;
        fill.color = new Color(0.7f, 0.8f, 1f);
        fillGO.transform.rotation = Quaternion.Euler(-18f, 150f, 0f);

        // ---------- 相机 ----------
        var camGO = new GameObject("Main Camera");
        camGO.tag = "MainCamera";
        var cam = camGO.AddComponent<Camera>();
        cam.clearFlags = CameraClearFlags.Skybox;
        cam.fieldOfView = 55f;
        camGO.transform.position = new Vector3(0f, 2.6f, -9.5f);
        camGO.transform.rotation = Quaternion.Euler(10f, 0f, 0f);

        // ---------- 地面 ----------
        var grassMat = MakeMat(new Color(0.20f, 0.42f, 0.22f), 0.05f);
        var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
        ground.name = "Ground";
        ground.transform.localScale = new Vector3(30f, 1f, 30f);
        ground.GetComponent<Renderer>().sharedMaterial = grassMat;

        BuildHills(grassMat);
        BuildPond();
        BuildPath();
        BuildTrees();
        BuildScatter(grassMat);

        // ---------- 花朵阵（三圈，带抖动，真花瓣） ----------
        var flowers = new List<Transform>();
        flowers.AddRange(FlowerRing("Flowers_Inner", 12, 2.6f, 0.4f));
        flowers.AddRange(FlowerRing("Flowers_Mid", 18, 4.6f, 0.7f));
        flowers.AddRange(FlowerRing("Flowers_Outer", 24, 6.8f, 1.0f));

        // ---------- 水晶簇（中央） ----------
        var crystals = new List<Transform>();
        crystals.AddRange(BuildCrystalCluster());

        // ---------- 萤火虫 / 浮尘（细微，非浓雾） ----------
        var fogGO = new GameObject("Fog");
        fogGO.transform.position = new Vector3(0f, 2.0f, 0f);
        var ps = fogGO.AddComponent<ParticleSystem>();
        var main = ps.main;
        main.startSize = 0.10f;
        main.startLifetime = 9f;
        main.startSpeed = 0.25f;
        main.maxParticles = 120;
        main.startColor = new Color(1f, 0.98f, 0.75f, 0.85f);
        main.gravityModifier = -0.01f;
        var shape = ps.shape;
        shape.shapeType = ParticleSystemShapeType.Box;
        shape.scale = new Vector3(16f, 3.5f, 16f);
        var em = ps.emission;
        em.rateOverTime = 4f;
        var pr = fogGO.GetComponent<ParticleSystemRenderer>();
        if (pr != null)
        {
            pr.renderMode = ParticleSystemRenderMode.Billboard;
            pr.material = CreateGlowMaterial();
            pr.sortingFudge = 0f;
        }

        // ---------- 水晶辉光 ----------
        var clGO = new GameObject("CrystalLight");
        clGO.transform.position = new Vector3(0f, 1.6f, 0f);
        var crystalLight = clGO.AddComponent<Light>();
        crystalLight.type = LightType.Point;
        crystalLight.range = 14f;
        crystalLight.intensity = 2.0f;
        crystalLight.color = new Color(0.45f, 0.9f, 1f);

        // ---------- 环境音 ----------
        var ambientGO = new GameObject("Ambient");
        var ambient = ambientGO.AddComponent<AudioSource>();
        ambient.loop = true;
        ambient.playOnAwake = true;
        ambient.volume = 0.2f;

        // ---------- 管理器 ----------
        var mgr = new GameObject("NeuroBloomManager");
        var bridge = mgr.AddComponent<BrainBridge>();
        var world = mgr.AddComponent<NeuroWorldController>();
        world.bridge = bridge;
        world.flowers = flowers.ToArray();
        world.crystals = crystals.ToArray();
        world.sunLight = sun;
        world.crystalLight = crystalLight;
        world.fog = ps;
        world.ambient = ambient;
        world.skyMaterial = skyMat;
        bridge.url = "ws://127.0.0.1:8765/ws/world";

        // ---------- 引导语（世界内文字） ----------
        var textGO = new GameObject("GuideText");
        textGO.transform.position = new Vector3(0f, 4.2f, 1.0f);
        var tm = textGO.AddComponent<TextMesh>();
        tm.text = "脑境向导已就绪…";
        tm.characterSize = 0.14f;
        tm.anchor = TextAnchor.MiddleCenter;
        tm.color = Color.white;
        tm.fontSize = 90;
        tm.font = GetDefaultFont();
        var gs = textGO.AddComponent<GuideTextUpdater>();
        gs.world = world;

        // ---------- 保存 ----------
        System.IO.Directory.CreateDirectory("Assets/Scenes");
        string path = "Assets/Scenes/NeuroBloom.unity";
        EditorSceneManager.SaveScene(scene, path);
        AssetDatabase.Refresh();
        Debug.Log($"[NeuroBloom] 场景已生成：{path}（直接按 Play 即可看到世界随脑波变化）");
        if (!Application.isBatchMode)
        {
            EditorUtility.DisplayDialog("NeuroBloom",
                "场景已搭建完成！\n\n1) 按 Play 运行\n2) 确保 Mac 上 bridge/eeg/agent 三个服务已启动\n(外部设备连线见教程)", "好");
        }
    }

    // ================= 地形 =================

    private static void BuildHills(Material grass)
    {
        var root = new GameObject("Hills").transform;
        for (int i = 0; i < 10; i++)
        {
            float a = (float)(_rnd.NextDouble() * Mathf.PI * 2.0);
            float r = 26f + (float)_rnd.NextDouble() * 26f;
            var h = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            h.name = "Hill_" + i;
            h.transform.SetParent(root);
            float s = 14f + (float)_rnd.NextDouble() * 16f;
            h.transform.position = new Vector3(Mathf.Cos(a) * r, -s * 0.42f, Mathf.Sin(a) * r);
            h.transform.localScale = new Vector3(s, s * 0.34f, s);
            h.GetComponent<Renderer>().sharedMaterial = grass;
            Object.DestroyImmediate(h.GetComponent<Collider>());
        }
    }

    private static void BuildPond()
    {
        var root = new GameObject("Pond").transform;
        var waterMat = MakeWater(new Color(0.16f, 0.42f, 0.55f, 0.72f));
        var w = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
        w.name = "Water";
        w.transform.SetParent(root);
        w.transform.position = new Vector3(-8.5f, -0.37f, 8.0f);
        w.transform.localScale = new Vector3(6.5f, 0.35f, 6.5f);
        w.GetComponent<Renderer>().sharedMaterial = waterMat;
        Object.DestroyImmediate(w.GetComponent<Collider>());

        // 岸边芦苇
        var reedMat = MakeMat(new Color(0.30f, 0.45f, 0.20f), 0f);
        for (int i = 0; i < 24; i++)
        {
            float a = (float)(_rnd.NextDouble() * Mathf.PI * 2.0);
            float r = 3.0f + (float)_rnd.NextDouble() * 0.4f;
            var reed = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            reed.name = "Reed_" + i;
            reed.transform.SetParent(root);
            float h = 0.8f + (float)_rnd.NextDouble() * 0.8f;
            reed.transform.position = new Vector3(-8.5f + Mathf.Cos(a) * r, h * 0.5f, 8.0f + Mathf.Sin(a) * r);
            reed.transform.localScale = new Vector3(0.04f, h * 0.5f, 0.04f);
            reed.transform.rotation = Quaternion.Euler((float)(_rnd.NextDouble() * 12 - 6), 0, (float)(_rnd.NextDouble() * 12 - 6));
            reed.GetComponent<Renderer>().sharedMaterial = reedMat;
            Object.DestroyImmediate(reed.GetComponent<Collider>());
        }
    }

    private static void BuildPath()
    {
        var root = new GameObject("Path").transform;
        var stoneMat = MakeMat(new Color(0.62f, 0.60f, 0.56f), 0.1f);
        for (float z = -9f; z <= -1.2f; z += 0.95f)
        {
            float x = Mathf.Sin(z * 0.5f) * 0.7f;
            var t = GameObject.CreatePrimitive(PrimitiveType.Cube);
            t.name = "StoneTile";
            t.transform.SetParent(root);
            t.transform.position = new Vector3(x, 0.03f, z);
            t.transform.rotation = Quaternion.Euler(0f, (float)(_rnd.NextDouble() * 30 - 15), 0f);
            t.transform.localScale = new Vector3(0.85f, 0.08f, 0.62f);
            t.GetComponent<Renderer>().sharedMaterial = stoneMat;
            Object.DestroyImmediate(t.GetComponent<Collider>());
        }
    }

    private static void BuildTrees()
    {
        var root = new GameObject("Trees").transform;
        var trunkMat = MakeMat(new Color(0.32f, 0.22f, 0.14f), 0f);
        var leafMatA = MakeMat(new Color(0.16f, 0.40f, 0.20f), 0f);
        var leafMatB = MakeMat(new Color(0.24f, 0.50f, 0.24f), 0f);
        for (int i = 0; i < 14; i++)
        {
            float a = (float)(i / 14.0 * Mathf.PI * 2.0 + _rnd.NextDouble() * 0.3);
            float r = 13f + (float)_rnd.NextDouble() * 9f;
            var tree = new GameObject("Tree_" + i).transform;
            tree.SetParent(root);
            tree.position = new Vector3(Mathf.Cos(a) * r, 0f, Mathf.Sin(a) * r);
            float th = 2.4f + (float)_rnd.NextDouble() * 1.6f;

            var trunk = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
            trunk.transform.SetParent(tree);
            trunk.transform.localPosition = new Vector3(0f, th * 0.5f, 0f);
            trunk.transform.localScale = new Vector3(0.34f, th * 0.5f, 0.34f);
            trunk.GetComponent<Renderer>().sharedMaterial = trunkMat;
            Object.DestroyImmediate(trunk.GetComponent<Collider>());

            var leaf = _rnd.NextDouble() > 0.5 ? leafMatA : leafMatB;
            for (int k = 0; k < 3; k++)
            {
                var f = GameObject.CreatePrimitive(PrimitiveType.Sphere);
                f.transform.SetParent(tree);
                float fs = (2.5f - k * 0.45f) * (0.85f + (float)_rnd.NextDouble() * 0.3f);
                f.transform.localPosition = new Vector3(
                    (float)(_rnd.NextDouble() * 0.7 - 0.35),
                    th + 0.5f + k * 0.75f,
                    (float)(_rnd.NextDouble() * 0.7 - 0.35));
                f.transform.localScale = new Vector3(fs, fs * 0.85f, fs);
                f.GetComponent<Renderer>().sharedMaterial = leaf;
                Object.DestroyImmediate(f.GetComponent<Collider>());
            }
        }
    }

    private static void BuildScatter(Material grass)
    {
        var root = new GameObject("Scatter").transform;
        var bushMat = MakeMat(new Color(0.20f, 0.45f, 0.26f), 0f);
        var rockMat = MakeMat(new Color(0.45f, 0.46f, 0.48f), 0.15f);

        for (int i = 0; i < 18; i++)   // 灌木
        {
            float a = (float)(_rnd.NextDouble() * Mathf.PI * 2.0);
            float r = 9f + (float)_rnd.NextDouble() * 8f;
            var b = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            b.name = "Bush_" + i;
            b.transform.SetParent(root);
            float s = 0.7f + (float)_rnd.NextDouble() * 0.8f;
            b.transform.position = new Vector3(Mathf.Cos(a) * r, s * 0.42f, Mathf.Sin(a) * r);
            b.transform.localScale = new Vector3(s, s * 0.75f, s);
            b.GetComponent<Renderer>().sharedMaterial = bushMat;
            Object.DestroyImmediate(b.GetComponent<Collider>());
        }
        for (int i = 0; i < 20; i++)   // 岩石
        {
            float a = (float)(_rnd.NextDouble() * Mathf.PI * 2.0);
            float r = 4.5f + (float)_rnd.NextDouble() * 12f;
            var c = GameObject.CreatePrimitive(PrimitiveType.Cube);
            c.name = "Rock_" + i;
            c.transform.SetParent(root);
            float s = 0.35f + (float)_rnd.NextDouble() * 0.75f;
            c.transform.position = new Vector3(Mathf.Cos(a) * r, s * 0.4f, Mathf.Sin(a) * r);
            c.transform.rotation = Quaternion.Euler(
                (float)(_rnd.NextDouble() * 360), (float)(_rnd.NextDouble() * 360), (float)(_rnd.NextDouble() * 360));
            c.transform.localScale = new Vector3(s, s * 0.8f, s * 1.2f);
            c.GetComponent<Renderer>().sharedMaterial = rockMat;
            Object.DestroyImmediate(c.GetComponent<Collider>());
        }
    }

    // ================= 花朵 =================

    private static readonly Color[] PetalPalette =
    {
        new Color(0.98f, 0.28f, 0.62f, 1f),
        new Color(1.00f, 0.55f, 0.80f, 1f),
        new Color(0.92f, 0.36f, 0.92f, 1f),
        new Color(0.52f, 0.45f, 1.00f, 1f),
        new Color(1.00f, 0.75f, 0.25f, 1f),
        new Color(0.30f, 0.72f, 1.00f, 1f),
        new Color(1.00f, 0.40f, 0.30f, 1f),
    };

    private static List<Transform> FlowerRing(string name, int count, float radius, float jitter)
    {
        var list = new List<Transform>();
        var root = new GameObject(name).transform;
        for (int i = 0; i < count; i++)
        {
            float a = i / (float)count * Mathf.PI * 2f + (float)(_rnd.NextDouble() * 0.15 - 0.075);
            float r = radius + (float)(_rnd.NextDouble() * 2 - 1) * jitter;
            var pos = new Vector3(Mathf.Cos(a) * r, 0f, Mathf.Sin(a) * r);
            var color = PetalPalette[_rnd.Next(PetalPalette.Length)];
            list.Add(CreateFlower(root, name + "_" + i, pos, color));
        }
        return list;
    }

    private static Transform CreateFlower(Transform parent, string name, Vector3 pos, Color petalColor)
    {
        var flower = new GameObject(name);
        flower.transform.SetParent(parent);
        flower.transform.position = pos;
        flower.transform.rotation = Quaternion.Euler(0f, (float)(_rnd.NextDouble() * 360), 0f);
        float h = 0.9f + (float)_rnd.NextDouble() * 0.5f;

        var stemMat = MakeMat(new Color(0.22f, 0.58f, 0.28f), 0f);
        var stem = GameObject.CreatePrimitive(PrimitiveType.Cylinder);
        stem.transform.SetParent(flower.transform);
        stem.transform.localPosition = new Vector3(0f, h * 0.5f, 0f);
        stem.transform.localScale = new Vector3(0.055f, h * 0.5f, 0.055f);
        stem.GetComponent<Renderer>().sharedMaterial = stemMat;
        Object.DestroyImmediate(stem.GetComponent<Collider>());

        // 叶子
        var leafMat = MakeMat(new Color(0.25f, 0.62f, 0.30f), 0f);
        for (int L = 0; L < 2; L++)
        {
            var leaf = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            leaf.transform.SetParent(flower.transform);
            leaf.transform.localPosition = new Vector3(L == 0 ? 0.12f : -0.12f, h * 0.45f, 0f);
            leaf.transform.localScale = new Vector3(0.22f, 0.03f, 0.10f);
            leaf.transform.localRotation = Quaternion.Euler(0f, 0f, L == 0 ? 25f : -25f);
            leaf.GetComponent<Renderer>().sharedMaterial = leafMat;
            Object.DestroyImmediate(leaf.GetComponent<Collider>());
        }

        // 花瓣（6 片）
        var head = new GameObject("Head");
        head.transform.SetParent(flower.transform);
        head.transform.localPosition = new Vector3(0f, h, 0f);
        var petalMat = MakeMat(petalColor, 0.12f);
        for (int p = 0; p < 6; p++)
        {
            float ang = p / 6f * 360f;
            var petal = GameObject.CreatePrimitive(PrimitiveType.Sphere);
            petal.transform.SetParent(head.transform);
            petal.transform.localRotation = Quaternion.Euler(0f, ang, 0f);
            petal.transform.localPosition = Quaternion.Euler(0f, ang, 0f) * new Vector3(0f, 0.02f, 0.16f);
            petal.transform.localScale = new Vector3(0.27f, 0.055f, 0.17f);
            petal.GetComponent<Renderer>().sharedMaterial = petalMat;
            Object.DestroyImmediate(petal.GetComponent<Collider>());
        }
        var core = GameObject.CreatePrimitive(PrimitiveType.Sphere);
        core.transform.SetParent(head.transform);
        core.transform.localScale = Vector3.one * 0.13f;
        core.GetComponent<Renderer>().sharedMaterial = MakeMat(new Color(1f, 0.9f, 0.45f), 0.5f);
        Object.DestroyImmediate(core.GetComponent<Collider>());

        flower.transform.localScale = Vector3.one * 0.7f;
        return flower.transform;
    }

    // ================= 水晶 =================

    private static List<Transform> BuildCrystalCluster()
    {
        var list = new List<Transform>();
        var root = new GameObject("CrystalCluster").transform;
        var mat = MakeMat(new Color(0.35f, 0.85f, 1f), 0.9f);
        var specs = new (Vector3 pos, float h, float w, float tilt)[]
        {
            (new Vector3(0f,0f,0f),      2.2f, 0.55f, 0f),
            (new Vector3(0.7f,0f,0.4f),  1.5f, 0.40f, 12f),
            (new Vector3(-0.6f,0f,0.5f), 1.8f, 0.45f, -10f),
            (new Vector3(0.3f,0f,-0.7f), 1.2f, 0.35f, 15f),
            (new Vector3(-0.5f,0f,-0.5f),1.0f, 0.30f, -14f),
        };
        for (int i = 0; i < specs.Length; i++)
        {
            var s = specs[i];
            var c = GameObject.CreatePrimitive(PrimitiveType.Cube);
            c.name = "Crystal_" + i;
            c.transform.SetParent(root);
            c.transform.localPosition = s.pos + new Vector3(0f, s.h * 0.5f, 0f);
            c.transform.rotation = Quaternion.Euler(s.tilt, i * 47f, s.tilt * 0.5f);
            c.transform.localScale = new Vector3(s.w, s.h, s.w * 0.7f);
            c.GetComponent<Renderer>().sharedMaterial = mat;
            Object.DestroyImmediate(c.GetComponent<Collider>());
            list.Add(c.transform);
        }
        return list;
    }

    // ================= 材质 =================

    private static Material MakeMat(Color c, float emissive)
    {
        Shader sh = Shader.Find("Standard");
        if (sh == null) sh = Shader.Find("Universal Render Pipeline/Lit");
        if (sh == null) sh = Shader.Find("Diffuse");
        var m = new Material(sh);
        m.color = c;
        if (m.HasProperty("_BaseColor")) m.SetColor("_BaseColor", c);
        if (m.HasProperty("_Glossiness")) m.SetFloat("_Glossiness", 0.1f);
        if (emissive > 0f && m.HasProperty("_EmissionColor"))
        {
            m.EnableKeyword("_EMISSION");
            m.SetColor("_EmissionColor", new Color(c.r * emissive * 0.8f, c.g * emissive * 0.8f, c.b * emissive * 0.8f));
            m.globalIlluminationFlags = MaterialGlobalIlluminationFlags.RealtimeEmissive;
        }
        return m;
    }

    private static Material MakeWater(Color c)
    {
        var m = MakeMat(c, 0f);
        if (m.HasProperty("_Mode"))
        {
            m.SetFloat("_Mode", 3f);                       // Transparent
            m.SetInt("_SrcBlend", (int)UnityEngine.Rendering.BlendMode.SrcAlpha);
            m.SetInt("_DstBlend", (int)UnityEngine.Rendering.BlendMode.OneMinusSrcAlpha);
            m.SetInt("_ZWrite", 0);
            m.DisableKeyword("_ALPHATEST_ON");
            m.EnableKeyword("_ALPHABLEND_ON");
            m.DisableKeyword("_ALPHAPREMULTIPLY_ON");
            m.renderQueue = 3000;
        }
        if (m.HasProperty("_Glossiness")) m.SetFloat("_Glossiness", 0.85f);
        if (m.HasProperty("_Metallic")) m.SetFloat("_Metallic", 0.2f);
        return m;
    }

    private static Material MakeSkybox()
    {
        Shader sh = Shader.Find("Skybox/Procedural");
        var m = new Material(sh);
        if (m.HasProperty("_SkyTint")) m.SetColor("_SkyTint", new Color(0.22f, 0.48f, 0.90f));
        if (m.HasProperty("_GroundColor")) m.SetColor("_GroundColor", new Color(0.80f, 0.88f, 1.00f));
        if (m.HasProperty("_AtmosphereThickness")) m.SetFloat("_AtmosphereThickness", 0.85f);
        if (m.HasProperty("_Exposure")) m.SetFloat("_Exposure", 1.15f);
        if (m.HasProperty("_SunSize")) m.SetFloat("_SunSize", 0.045f);
        if (m.HasProperty("_SunDisk")) m.SetFloat("_SunDisk", 2f);
        return m;
    }

    private static Material CreateGlowMaterial()
    {
        // 柔和圆形光点贴图（萤火虫）
        const int S = 64;
        var tex = new Texture2D(S, S, TextureFormat.RGBA32, false);
        var cols = new Color[S * S];
        for (int y = 0; y < S; y++)
        {
            for (int x = 0; x < S; x++)
            {
                float dx = (x + 0.5f) / S * 2f - 1f;
                float dy = (y + 0.5f) / S * 2f - 1f;
                float d = Mathf.Sqrt(dx * dx + dy * dy);
                float a = Mathf.Clamp01(1f - d);
                a = a * a * a;
                cols[y * S + x] = new Color(1f, 1f, 1f, a);
            }
        }
        tex.SetPixels(cols);
        tex.Apply();

        System.IO.Directory.CreateDirectory("Assets/Textures");
        System.IO.Directory.CreateDirectory("Assets/Materials");
        const string texPath = "Assets/Textures/GlowPuff.asset";
        AssetDatabase.DeleteAsset(texPath);
        AssetDatabase.CreateAsset(tex, texPath);

        Shader sh = Shader.Find("Legacy Shaders/Particles/Additive");
        if (sh == null) sh = Shader.Find("Mobile/Particles/Additive");
        if (sh == null) sh = Shader.Find("Particles/Standard Unlit");
        if (sh == null) sh = Shader.Find("Sprites/Default");
        var mat = new Material(sh);
        mat.mainTexture = tex;
        if (mat.HasProperty("_TintColor")) mat.SetColor("_TintColor", new Color(1f, 0.96f, 0.7f, 0.7f));

        const string matPath = "Assets/Materials/GlowPuff.mat";
        AssetDatabase.DeleteAsset(matPath);
        AssetDatabase.CreateAsset(mat, matPath);
        return mat;
    }

    private static Font GetDefaultFont()
    {
        try { return Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf"); }
        catch { }
        try { return Resources.GetBuiltinResource<Font>("Arial.ttf"); }
        catch { }
        return null;
    }
}
