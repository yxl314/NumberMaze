/**
 * @file Difficulty.h
 * @brief 难度级别定义及其对应的打乱步数。
 *
 * 本文件属于 Core 层，为纯 C++ 实现，不依赖任何 GUI 框架。
 */

#ifndef CORE_DIFFICULTY_H
#define CORE_DIFFICULTY_H

namespace core {

/**
 * @brief 游戏难度级别。
 *
 * 枚举值与界面菜单的排列顺序一致，可直接用作索引。
 */
enum class Difficulty {
    Easy = 0,   ///< 简单。
    Normal = 1, ///< 普通，默认难度。
    Hard = 2,   ///< 困难。
    Expert = 3  ///< 专家。
};

/**
 * @brief 取得指定难度对应的打乱步数。
 *
 * 打乱步数指从已解状态出发执行的合法移动次数。步数越多，
 * 局面离已解状态越远，通常越难。
 *
 * @param level 难度级别。
 * @return 该难度应执行的打乱步数。
 *
 * @note 传入未列举的枚举值时返回普通难度的步数作为兜底。
 */
inline int scrambleSteps(Difficulty level)
{
    switch (level) {
    case Difficulty::Easy:   return 20;
    case Difficulty::Normal: return 40;
    case Difficulty::Hard:   return 80;
    case Difficulty::Expert: return 160;
    }
    return 60;
}

/**
 * @brief 取得难度的中文名称。
 *
 * 名称属于游戏自身的属性而非界面装饰，故放在 Core 层统一维护，
 * 避免各界面重复定义。返回 UTF-8 编码的 C 字符串。
 *
 * @param level 难度级别。
 * @return 指向静态存储区的名称字符串，调用者不得释放。
 */
inline const char *difficultyName(Difficulty level)
{
    switch (level) {
    case Difficulty::Easy:   return "简单";
    case Difficulty::Normal: return "普通";
    case Difficulty::Hard:   return "困难";
    case Difficulty::Expert: return "专家";
    }
    return "普通";
}

} // namespace core

#endif // CORE_DIFFICULTY_H
