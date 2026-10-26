// NeuroCritters.cs —— 小鸟 / 蝴蝶的程序化运动。
// 挂在每只生物的根节点上，翅膀作为其子物体。
//
// 不使用 iTween 或任何第三方动画库：位置由正弦函数直接求出，转向用 Slerp 平滑。
// 编辑器内也运行（[ExecuteAlways]），所以渲染预览图时生物在正确位置上。
using UnityEngine;

[ExecuteAlways]
[AddComponentMenu("NeuroBloom/Critters")]
public class NeuroCritters : MonoBehaviour
{
    public enum Mode { Bird, Butterfly }

    [Header("类型")]
    public Mode mode = Mode.Bird;

    [Header("路径")]
    public Vector3 center = Vector3.zero;
    public float radius = 6.0f;
    public float height = 5.0f;
    public float speed = 0.35f;
    public float phase = 0.0f;
    public float wobble = 0.6f;
    public float ellipseZ = 0.80f;   // 小鸟：z 方向压扁比例；蝴蝶：8 字纵向比例

    [Header("翅膀")]
    public Transform wingL;
    public Transform wingR;
    public float flapSpeed = 9.0f;
    public float flapAmount = 55.0f;

    [Header("转向")]
    public float bankAmount = 18.0f;
    public float turnLerp = 4.0f;

    private Vector3 _prevPos;
    private bool _init;
    private Quaternion _wingLBase = Quaternion.identity;
    private Quaternion _wingRBase = Quaternion.identity;

    private void OnEnable()
    {
        Init();
    }

    private void Init()
    {
        if (wingL == null)
        {
            Transform t = transform.Find("WingL");
            if (t != null) wingL = t;
        }
        if (wingR == null)
        {
            Transform t = transform.Find("WingR");
            if (t != null) wingR = t;
        }

        if (wingL != null) _wingLBase = wingL.localRotation;
        if (wingR != null) _wingRBase = wingR.localRotation;

        transform.position = Sample(0f);
        _prevPos = transform.position;
        _init = true;
    }

    private Vector3 Sample(float t)
    {
        Vector3 p;

        if (mode == Mode.Bird)
        {
            // 缓圆轨迹，z 方向压扁，高度上下起伏 —— 像在树冠间盘旋滑翔
            p = new Vector3(
                center.x + Mathf.Cos(t) * radius,
                center.y + height + Mathf.Sin(t * 1.7f) * wobble,
                center.z + Mathf.Sin(t) * radius * ellipseZ);
        }
        else
        {
            // 8 字轨迹 —— 蝴蝶的典型飞舞
            p = new Vector3(
                center.x + Mathf.Sin(t) * radius,
                center.y + height + Mathf.Sin(t * 3.0f) * wobble * 0.35f,
                center.z + Mathf.Sin(t * 2.0f) * radius * ellipseZ);
        }

        return p;
    }

    private void Update()
    {
        if (!_init) Init();

        float t = Time.realtimeSinceStartup * speed + phase;
        Vector3 pos = Sample(t);
        transform.position = pos;

        Vector3 dir = pos - _prevPos;
        if (dir.sqrMagnitude > 1e-8f)
        {
            Quaternion look = Quaternion.LookRotation(dir.normalized, Vector3.up);
            // 用轨迹的横向曲率做侧倾，转弯时压翅
            float bank = Mathf.Clamp(-(Sample(t + 0.15f).x - pos.x) * 60f, -bankAmount, bankAmount);
            look *= Quaternion.Euler(0f, 0f, bank);
            transform.rotation = Quaternion.Slerp(transform.rotation, look, turnLerp * Mathf.Max(Time.deltaTime, 1f / 60f));
        }
        _prevPos = pos;

        float f = Mathf.Sin(Time.realtimeSinceStartup * flapSpeed + phase) * flapAmount;
        if (wingL != null) wingL.localRotation = _wingLBase * Quaternion.Euler(0f, 0f, f);
        if (wingR != null) wingR.localRotation = _wingRBase * Quaternion.Euler(0f, 0f, -f);
    }
}
