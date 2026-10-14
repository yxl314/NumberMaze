#ifndef CORE_DIFFICULTY_H
#define CORE_DIFFICULTY_H

namespace core {

// 难度级别。数值用于界面排序，scrambleSteps 为打乱所需的合法移动步数。
enum class Difficulty {
    Easy = 0,
    Normal = 1,
    Hard = 2,
    Expert = 3
};

// 难度对应的打乱步数。步数越多，局面离已解状态越远，通常越难。
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

// 难度名称（中文）。放在 Core 里是有意为之：
// 它是游戏本身的属性，而非界面装饰。
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
