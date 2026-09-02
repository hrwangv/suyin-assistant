import numpy as np
import matplotlib.pyplot as plt

A_vals = [0, 10, 20, 30, 40, 50, 60, 100]
x_min, x_max = -130, 50
y_lim = 5

plt.figure(figsize=(12, 8))
colors = plt.cm.viridis(np.linspace(0.1, 0.9, len(A_vals)))

for i, A in enumerate(A_vals):
    # 左支：x < -A
    x_left = np.linspace(x_min, -A - 0.001, 500)
    y_left = 1 / (x_left + A)

    # 右支：x > -A
    x_right = np.linspace(-A + 0.001, x_max, 500)
    y_right = 1 / (x_right + A)

    plt.plot(x_left, y_left, color=colors[i], linewidth=1.5)
    plt.plot(x_right, y_right, color=colors[i], linewidth=1.5, label=f'A = {A}')

# 坐标轴
plt.axhline(0, color='black', linewidth=0.6)
plt.axvline(0, color='black', linewidth=0.6)

plt.xlim(x_min, x_max)
plt.ylim(-y_lim, y_lim)
plt.xlabel('x', fontsize=12)
plt.ylabel('y', fontsize=12)
plt.title('函数族 y = 1/(x + A) 的图像', fontsize=14)
plt.legend(bbox_to_anchor=(1.02, 1), loc='upper left', fontsize=10)
plt.grid(alpha=0.3)
plt.tight_layout()

plt.savefig('reciprocal_family.png', dpi=300, bbox_inches='tight')
plt.show()