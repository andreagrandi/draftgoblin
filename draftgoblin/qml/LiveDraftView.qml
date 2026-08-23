pragma ComponentBehavior: Bound

import QtQuick 2.15
import QtQuick.Controls 2.15
import QtQuick.Layouts 1.15

Item {
    id: root

    required property var sessionState
    required property var recommendationModel
    readonly property bool hasDraft: sessionState.draft !== null
        && sessionState.draft !== undefined
    required property bool narrow

    readonly property var selectedRecommendation: {
        if (!sessionState.recommendations || !sessionState.recommendations.cards) return null
        for (let index = 0; index < sessionState.recommendations.cards.length; index++) {
            const recommendation = sessionState.recommendations.cards[index]
            if (recommendation.card.grp_id === sessionState.recommendations.selected_grp_id)
                return recommendation
        }
        return sessionState.recommendations.cards.length > 0
            ? sessionState.recommendations.cards[0] : null
    }

    ColumnLayout {
        anchors.fill: parent
        spacing: Theme.gutter

        RowLayout {
            Layout.fillWidth: true
            spacing: 12

            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2

                Label {
                    text: root.sessionState.draft
                        ? "Pack " + root.sessionState.draft.pack_number
                            + " · Pick " + root.sessionState.draft.pick_number
                        : "Live draft readiness"
                    color: Theme.text
                    font.pixelSize: 22
                    font.bold: true
                }

                Label {
                    text: root.sessionState.recommendations.cards.length > 0
                        ? root.sessionState.recommendations.cards.length + " cards available · Close pick"
                        : "Start Draftgoblin before entering a Quick Draft"
                    color: Theme.textMuted
                }
            }

            Label {
                visible: !root.narrow
                text: "RANK BY"
                color: Theme.textMuted
                font.pixelSize: 10
                font.bold: true
            }

            ComboBox {
                id: rankingSelector
                Layout.preferredWidth: root.narrow ? 130 : 166
                model: [
                    { key: "score", label: "DG Score" },
                    { key: "win_rate", label: "17L WR" },
                    { key: "alsa", label: "ALSA" },
                    { key: "mana_value", label: "Mana value" }
                ]
                textRole: "label"
                currentIndex: {
                    for (let index = 0; index < model.length; index++)
                        if (model[index].key === root.sessionState.recommendations.ranking_mode)
                            return index
                    return 0
                }
                Accessible.name: "Recommendation ranking"
                onActivated: sessionProvider.changeRanking(model[currentIndex].key)
            }
        }

        StateBanner {
            Layout.fillWidth: true
            sessionState: root.sessionState
        }

        Rectangle {
            visible: !root.hasDraft
            Layout.fillWidth: true
            Layout.fillHeight: true
            color: Theme.surfaceLow
            border.color: Theme.outline
            border.width: 1
            radius: Theme.radius

            ColumnLayout {
                anchors.centerIn: parent
                width: Math.min(parent.width - 48, 520)
                spacing: 14

                Label {
                    Layout.fillWidth: true
                    text: "Ready for your next Quick Draft"
                    color: Theme.text
                    font.pixelSize: 22
                    font.bold: true
                    horizontalAlignment: Text.AlignHCenter
                }

                Label {
                    Layout.fillWidth: true
                    text: "Draftgoblin follows Arena automatically and never writes to the game."
                    color: Theme.textMuted
                    horizontalAlignment: Text.AlignHCenter
                    wrapMode: Text.WordWrap
                }

                Repeater {
                    model: [
                        ["Card metadata", root.sessionState.card_data.message],
                        ["Arena log", "Watching Player.log"],
                        ["Arena account", root.sessionState.active_account
                            ? root.sessionState.active_account.screen_name : "Not detected"],
                        ["Draft", root.sessionState.status.message],
                        ["Ratings", root.sessionState.ratings.message]
                    ]

                    delegate: Rectangle {
                        required property var modelData

                        Layout.fillWidth: true
                        Layout.preferredHeight: 46
                        color: Theme.surface
                        radius: Theme.radius

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: 12
                            anchors.rightMargin: 12

                            Label {
                                Layout.preferredWidth: 126
                                text: modelData[0]
                                color: Theme.text
                                font.bold: true
                            }
                            Label {
                                Layout.fillWidth: true
                                text: modelData[1]
                                color: Theme.textMuted
                                elide: Text.ElideRight
                            }
                        }
                    }
                }
            }
        }

        RowLayout {
            visible: !root.narrow && root.hasDraft
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.gutter

            Rectangle {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 430
                color: "transparent"

                ColumnLayout {
                    anchors.fill: parent
                    spacing: 6

                    RowLayout {
                        Layout.fillWidth: true
                        Layout.leftMargin: 12
                        Layout.rightMargin: 12

                        Label { Layout.preferredWidth: 26; text: "#"; color: Theme.textMuted; font.pixelSize: 10 }
                        Label { Layout.fillWidth: true; text: "CARD"; color: Theme.textMuted; font.pixelSize: 10 }
                        Label { Layout.preferredWidth: 64; text: "COLOR"; color: Theme.textMuted; font.pixelSize: 10 }
                        Label { Layout.preferredWidth: 52; text: "DG"; color: Theme.textMuted; font.pixelSize: 10; horizontalAlignment: Text.AlignRight }
                        Label { Layout.preferredWidth: 58; text: "17L WR"; color: Theme.textMuted; font.pixelSize: 10; horizontalAlignment: Text.AlignRight }
                        Label { Layout.preferredWidth: 34; text: "GRADE"; color: Theme.textMuted; font.pixelSize: 10 }
                        Label { Layout.preferredWidth: 84; text: "FIT"; color: Theme.textMuted; font.pixelSize: 10; horizontalAlignment: Text.AlignRight }
                    }

                    ListView {
                        Layout.fillWidth: true
                        Layout.fillHeight: true
                        spacing: 5
                        clip: true
                        model: root.recommendationModel
                        Accessible.name: "Ranked recommendations"

                        delegate: RecommendationRow {
                            required property var modelData

                            width: ListView.view.width
                            recommendation: modelData
                            selected: root.sessionState.recommendations.selected_grp_id === modelData.card.grp_id
                            wide: true
                            onChosen: grpId => sessionProvider.chooseRecommendation(grpId)
                        }
                    }
                }
            }

            CardPreview {
                Layout.preferredWidth: 292
                Layout.fillHeight: true
                recommendation: root.selectedRecommendation
                loading: root.sessionState.card_data.phase === "loading"
            }

            PoolSummaryPanel {
                Layout.preferredWidth: 252
                Layout.fillHeight: true
                pool: root.sessionState.pool
            }
        }

        ColumnLayout {
            visible: root.narrow && root.hasDraft
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            ListView {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 260
                spacing: 6
                clip: true
                model: root.recommendationModel
                Accessible.name: "Ranked recommendations"

                delegate: RecommendationRow {
                    required property var modelData

                    width: ListView.view.width
                    recommendation: modelData
                    selected: root.sessionState.recommendations.selected_grp_id === modelData.card.grp_id
                    wide: false
                    onChosen: grpId => sessionProvider.chooseRecommendation(grpId)
                }
            }

            TabBar {
                id: detailTabs
                Layout.fillWidth: true

                TabButton { text: "Card details" }
                TabButton { text: "Pool" }
            }

            StackLayout {
                Layout.fillWidth: true
                Layout.preferredHeight: 330
                currentIndex: detailTabs.currentIndex

                CardPreview {
                    recommendation: root.selectedRecommendation
                    loading: root.sessionState.card_data.phase === "loading"
                }

                PoolSummaryPanel {
                    pool: root.sessionState.pool
                }
            }
        }
    }
}

